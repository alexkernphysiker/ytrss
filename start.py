#!/usr/bin/env python3
"""Run the web server, feed updater and transcriber in one shared process."""

from collections import deque
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import logging
from pathlib import Path
import signal
import sys
from threading import Event, RLock, Thread, local
import traceback

from werkzeug.serving import make_server

from config import get_config, save_config


UPDATE_INTERVAL = 1
TRANSCRIPTION_INTERVAL = 1
LOG_MAX_LINES = 20_000
_worker_output = local()


class ThreadOutput:
    """Route Python output to this worker's log, or to the original stream.

    Redirecting stdout separately inside each worker would affect all threads.
    Install this router once instead; each worker owns its log stream.
    """

    def __init__(self, fallback):
        self.fallback = fallback

    def __getattr__(self, name):
        return getattr(getattr(_worker_output, "stream", self.fallback), name)

    def write(self, text):
        return getattr(_worker_output, "stream", self.fallback).write(text)

    def flush(self):
        return getattr(_worker_output, "stream", self.fallback).flush()


class ConsoleLogOutput:
    """Copy console output to one shared log, serializing concurrent writes."""

    def __init__(self, console, log, lock):
        self.console = console
        self.log = log
        self.lock = lock

    def __getattr__(self, name):
        return getattr(self.console, name)

    def write(self, text):
        with self.lock:
            if self.log is not None:
                self.log.write(text)
            return self.console.write(text)

    def flush(self):
        with self.lock:
            if self.log is not None:
                self.log.flush()
            self.console.flush()

    def trim(self):
        with self.lock:
            if self.log is not None:
                self.log.flush()
                trim_log(self.log.name)


def redirect_logging_streams(replacements):
    """Update console handlers that cached stdout/stderr before redirection."""
    loggers = [logging.getLogger()]
    handlers = set()
    while loggers:
        logger = loggers.pop()
        loggers.extend(logger.getChildren())
        handlers.update(logger.handlers)
    for handler in handlers:
        if not isinstance(handler, logging.StreamHandler):
            continue
        for original, replacement in replacements:
            stream = handler.stream
            while stream is not original and type(stream) is ThreadOutput:
                stream = stream.fallback
            if stream is original:
                handler.setStream(replacement)
                break


@contextmanager
def application_log(path="ytrss.log"):
    """Append main/server output and yield a synchronized trimming callback."""
    console_out, console_err = sys.stdout, sys.stderr
    with open(path, "a", encoding="utf-8", errors="backslashreplace", buffering=1) as stream:
        lock = RLock()
        stdout = ThreadOutput(ConsoleLogOutput(console_out, stream, lock))
        stderr = ThreadOutput(ConsoleLogOutput(console_err, stream, lock))
        stdout.fallback.trim()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                redirect_logging_streams(((console_out, stdout), (console_err, stderr)))
                yield stdout.fallback.trim
            except BaseException as error:
                # Python will report the uncaught exception to the restored console.
                # Save it here before the log closes, without printing it twice there.
                if not isinstance(error, SystemExit) or error.code not in (None, 0):
                    with lock:
                        traceback.print_exc(file=stream)
                raise
            finally:
                # Include handlers created during this run (such as Werkzeug's).
                redirect_logging_streams(((stdout, console_out), (stderr, console_err)))
                # A finishing HTTP request may still hold its WSGI error stream.
                with lock:
                    stdout.fallback.log = stderr.fallback.log = None


def trim_log(path):
    """Keep the last LOG_MAX_LINES, preserving raw subprocess output bytes.

    The caller must flush pending writes and exclude concurrent writers.
    Worker logs are idle before a pass; the application log shares a lock
    between trimming and all writes.
    """
    with open(path, "r+b") as stream:
        lines = deque(stream, maxlen=LOG_MAX_LINES + 1)
        if len(lines) > LOG_MAX_LINES:
            lines.popleft()
            stream.seek(0)
            stream.writelines(lines)
            stream.truncate()


@contextmanager
def worker_log(path):
    if path is None:
        yield
        return
    with open(path, "a", encoding="utf-8", buffering=1) as stream:
        trim_log(path)
        _worker_output.stream = stream
        try:
            yield
        finally:
            del _worker_output.stream


def run_periodically(action, interval, stop_event, log_path=None, before_pass=None):
    """Run immediately, then wait after each pass, including failed passes."""
    try:
        while not stop_event.is_set():
            try:
                if before_pass is not None:
                    before_pass()
                with worker_log(log_path):
                    try:
                        action()
                    except Exception:
                        traceback.print_exc()
            except Exception:
                # Even a log-file error must not silently kill the worker.
                traceback.print_exc()
            if stop_event.wait(interval):
                break
    finally:
        stop_event.set()


def run_application(app, update, transcribe, stop_event, before_pass=None):
    cfg = get_config()
    server = make_server(cfg["host"], cfg["port"], app, threaded=True)

    def serve():
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            stop_event.set()

    threads = [
        Thread(target=serve, name="web-server"),
        Thread(
            target=run_periodically, name="feed-updater",
            args=(update, UPDATE_INTERVAL, stop_event, "ytrss_upd.log", before_pass),
        ),
        Thread(
            target=run_periodically, name="transcriber",
            args=(transcribe, TRANSCRIPTION_INTERVAL, stop_event, "ytrss_transcribe.log", before_pass),
        ),
    ]
    stdout = sys.stdout if isinstance(sys.stdout, ThreadOutput) else ThreadOutput(sys.stdout)
    stderr = sys.stderr if isinstance(sys.stderr, ThreadOutput) else ThreadOutput(sys.stderr)
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            for thread in threads:
                thread.start()
            stop_event.wait()
        finally:
            stop_event.set()
            if threads[0].is_alive():
                server.shutdown()
            for thread in threads:
                if thread.ident is not None:
                    thread.join()
            server.server_close()


def _main(before_pass=None):
    # Relative runtime paths remain rooted in the launcher's working directory.
    Path("yt-video").mkdir(exist_ok=True)
    save_config()
    from ytrss import app
    from ytrss_upd import run_update
    from ytrss_transcribe import run_transcription

    stop_event = Event()

    def request_stop(signum, frame):
        stop_event.set()

    previous_handlers = {
        sig: signal.signal(sig, request_stop)
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        run_application(app, run_update, run_transcription, stop_event, before_pass)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def main():
    with application_log() as trim_application_log:
        _main(trim_application_log)


if __name__ == "__main__":
    main()
