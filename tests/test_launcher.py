from contextlib import chdir, ExitStack, redirect_stderr
import io
import json
from pathlib import Path
from queue import Queue
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread, current_thread
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import config
import start


class LauncherTests(unittest.TestCase):
    def test_failed_pass_retries_and_wait_happens_after_every_pass(self):
        class Stop:
            def __init__(self):
                self.stopped = False
                self.waits = []

            def is_set(self):
                return self.stopped

            def set(self):
                self.stopped = True

            def wait(self, delay):
                self.waits.append(delay)
                return len(self.waits) == 3

        stop = Stop()
        calls = []

        def action():
            calls.append(len(stop.waits))
            if len(calls) == 1:
                raise RuntimeError("fixture failure")

        with redirect_stderr(io.StringIO()) as errors:
            start.run_periodically(action, 60, stop)
        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(stop.waits, [60, 60, 60])
        self.assertIn("fixture failure", errors.getvalue())
        self.assertEqual((start.UPDATE_INTERVAL, start.TRANSCRIPTION_INTERVAL), (60, 60))

    def test_idle_wait_is_interruptible(self):
        stop = Event()
        called = Event()
        worker = Thread(target=start.run_periodically, args=(called.set, 60, stop))
        worker.start()
        try:
            self.assertTrue(called.wait(5))
        finally:
            stop.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_three_workers_are_independent_and_logs_do_not_mix(self):
        with TemporaryDirectory() as directory, chdir(directory), ExitStack() as stack:
            store = config.ConfigStore("ytrss_config.json")
            with store.edit() as cfg:
                cfg["port"] = 0
            stack.enter_context(patch.object(config, "_store", store))
            Path("ytrss_upd.log").write_text("update history\n" * start.LOG_MAX_LINES)
            Path("ytrss_transcribe.log").write_text("transcribe history\n" * start.LOG_MAX_LINES)
            stop = Event()
            release = Event()
            ready = Barrier(3)
            ports = Queue()
            worker_names = Queue()
            make_server = start.make_server

            def make(*args, **kwargs):
                server = make_server(*args, **kwargs)
                ports.put(server.server_port)
                return server

            stack.enter_context(patch.object(start, "make_server", make))

            def app(environ, respond):
                worker_names.put("web-server-request")
                respond("200 OK", [("Content-Type", "text/plain")])
                return [b"ready"]

            def action(label):
                worker_names.put(current_thread().name)
                print(f"{label} stdout")
                print(f"{label} stderr", file=sys.stderr)
                subprocess.run(
                    [sys.executable, "-c", f"print('{label} subprocess')"],
                    stdout=sys.stdout, stderr=sys.stderr, check=True,
                )
                ready.wait(timeout=5)
                if not release.wait(5):
                    raise TimeoutError("worker was not released")

            launcher = Thread(target=start.run_application, args=(
                app, lambda: action("update"), lambda: action("transcribe"), stop,
            ))
            launcher.start()
            try:
                port = ports.get(timeout=5)
                ready.wait(timeout=5)
                # Both periodic workers are blocked here; HTTP must still work.
                with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                    self.assertEqual(response.read(), b"ready")
            finally:
                stop.set()
                release.set()
                launcher.join(timeout=10)
            self.assertFalse(launcher.is_alive())
            self.assertEqual({worker_names.get_nowait() for _ in range(3)}, {
                "feed-updater", "transcriber", "web-server-request",
            })
            update_log = Path("ytrss_upd.log").read_text()
            transcription_log = Path("ytrss_transcribe.log").read_text()
            for suffix in ("stdout", "stderr", "subprocess"):
                self.assertIn(f"update {suffix}", update_log)
                self.assertIn(f"transcribe {suffix}", transcription_log)
            self.assertNotIn("transcribe", update_log)
            self.assertNotIn("update", transcription_log)
            self.assertIn("update history", update_log)
            self.assertIn("transcribe history", transcription_log)
            self.assertEqual(len(update_log.splitlines()), start.LOG_MAX_LINES + 3)
            self.assertEqual(len(transcription_log.splitlines()), start.LOG_MAX_LINES + 3)

    def test_main_starts_actual_app_and_handles_sigint_and_sigterm(self):
        script = Path(start.__file__).resolve()
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=stop_signal), TemporaryDirectory() as directory:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                settings = config.default_config()
                settings["port"] = port
                Path(directory, "ytrss_config.json").write_text(json.dumps(settings))
                process = subprocess.Popen(
                    [sys.executable, str(script)], cwd=directory,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        if process.poll() is not None:
                            out, err = process.communicate()
                            self.fail(f"Launcher exited early: {out}\n{err}")
                        try:
                            with urlopen(f"http://127.0.0.1:{port}/subscription", timeout=0.2) as response:
                                self.assertIn(b"Auto-transcription", response.read())
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                self.fail("Launcher did not start HTTP server")
                            time.sleep(0.05)
                    process.send_signal(stop_signal)
                    out, err = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, out + err)
                    self.assertTrue(Path(directory, "yt-video").is_dir())
                    self.assertTrue(Path(directory, "ytrss_upd.log").exists())
                    self.assertTrue(Path(directory, "ytrss_transcribe.log").exists())
                    self.assertFalse(list(Path(directory).glob("*.txt")))
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()


if __name__ == "__main__":
    unittest.main()
