#!/usr/bin/env python3
"""Run the web server, feed updater and transcriber in one shared process."""

from collections import deque
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
import signal
import sys
from threading import Event, Thread, local
import traceback

from werkzeug.serving import make_server

from config import get_config, save_config


UPDATE_INTERVAL = 60
TRANSCRIPTION_INTERVAL = 60
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


def trim_worker_log(path):
    """Keep the last LOG_MAX_LINES, preserving raw subprocess output bytes.

    Called only before a pass, when this worker and its synchronous
    subprocesses are not writing. The two workers own separate log files.
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
        trim_worker_log(path)
        _worker_output.stream = stream
        try:
            yield
        finally:
            del _worker_output.stream


def run_periodically(action, interval, stop_event, log_path=None):
    """Run immediately, then wait after each pass, including failed passes."""
    try:
        while not stop_event.is_set():
            try:
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


def run_application(app, update, transcribe, stop_event):
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
            args=(update, UPDATE_INTERVAL, stop_event, "ytrss_upd.log"),
        ),
        Thread(
            target=run_periodically, name="transcriber",
            args=(transcribe, TRANSCRIPTION_INTERVAL, stop_event, "ytrss_transcribe.log"),
        ),
    ]
    with redirect_stdout(ThreadOutput(sys.stdout)), redirect_stderr(ThreadOutput(sys.stderr)):
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


def main():
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
        run_application(app, run_update, run_transcription, stop_event)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
