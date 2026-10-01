from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import logging
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
import unittest
from unittest.mock import patch

from flask import Flask

import start


class ApplicationLogTests(unittest.TestCase):
    def test_startup_preserves_limit_and_trims_oversized_history_as_raw_bytes(self):
        lines = [f"Рядок {number}".encode() + b"\xff\r\n"
                 for number in range(start.LOG_MAX_LINES + 2)]
        lines.append(b"last incomplete line")
        for count in (start.LOG_MAX_LINES, len(lines)):
            with self.subTest(lines=count), TemporaryDirectory() as directory:
                path = Path(directory, "ytrss.log")
                history = lines[:count]
                path.write_bytes(b"".join(history))
                before = path.stat()
                with start.application_log(path):
                    self.assertEqual(path.read_bytes(), b"".join(history[-start.LOG_MAX_LINES:]))
                self.assertEqual(path.stat().st_ino, before.st_ino)
                if count == start.LOG_MAX_LINES:
                    self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_each_pass_trims_application_log_after_failed_and_empty_passes(self):
        class Stop:
            stopped = False
            waits = 0

            def is_set(self):
                return self.stopped

            def set(self):
                self.stopped = True

            def wait(self, delay):
                self.waits += 1
                if self.waits == 3:
                    return True
                print(f"between passes {self.waits}")
                return False

        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = Path(directory, "ytrss.log")
            worker_path = Path(directory, "worker.log")
            history = [f"message {number}\n" for number in range(start.LOG_MAX_LINES + 5)]
            passes = []

            def action():
                passes.append(path.read_text().splitlines(keepends=True))
                if len(passes) == 1:
                    raise RuntimeError("failed pass fixture")

            with start.application_log(path) as before_pass:
                sys.stdout.write("".join(history))
                self.assertEqual(len(path.read_text().splitlines()), start.LOG_MAX_LINES + 5)
                start.run_periodically(action, 1, Stop(), worker_path, before_pass)
            self.assertEqual(len(passes), 3)
            for index, contents in enumerate(passes):
                additions = [f"between passes {number}\n" for number in range(1, index + 1)]
                self.assertEqual(contents, (history + additions)[-start.LOG_MAX_LINES:])
            self.assertEqual(path.read_text(), "".join(passes[-1]))
            self.assertIn("RuntimeError: failed pass fixture", worker_path.read_text())

    def test_concurrent_trimming_and_writes_keep_exact_tail_and_complete_console(self):
        with TemporaryDirectory() as directory, patch.object(start, "LOG_MAX_LINES", 50):
            path = Path(directory, "ytrss.log")
            console = io.StringIO()
            ready = Barrier(6)

            def write(index):
                ready.wait(timeout=5)
                stream = sys.stdout if index % 2 == 0 else sys.stderr
                for number in range(200):
                    stream.write(f"потік {index}, повідомлення {number}\n")

            def trim_repeatedly(trim):
                ready.wait(timeout=5)
                for _ in range(40):
                    trim()

            with redirect_stdout(console), redirect_stderr(console), start.application_log(path) as trim:
                with ThreadPoolExecutor(max_workers=6) as workers:
                    futures = [workers.submit(write, index) for index in range(4)]
                    futures.extend(workers.submit(trim_repeatedly, trim) for _ in range(2))
                    for future in futures:
                        future.result(timeout=10)
                # Trimming must also flush a final write without a newline.
                sys.stderr.write("незавершений рядок")
                trim()
            output = console.getvalue().splitlines(keepends=True)
            self.assertCountEqual(output[:-1], [
                f"потік {index}, повідомлення {number}\n"
                for index in range(4) for number in range(200)
            ])
            self.assertEqual(output[-1], "незавершений рядок")
            self.assertEqual(path.read_text(), "".join(output[-start.LOG_MAX_LINES:]))

    def test_output_and_errors_append_and_remain_on_console(self):
        with TemporaryDirectory() as directory:
            path = Path(directory, "ytrss.log")
            path.write_text("previous run\n")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                with start.application_log(path):
                    print("application message")
                    print("application error", file=sys.stderr)
                    saved_error_stream = sys.stderr
                saved_error_stream.write("late shutdown message\n")
                saved_error_stream.flush()
                with start.application_log(path):
                    print("next run")
            self.assertEqual(path.read_text(),
                             "previous run\napplication message\napplication error\nnext run\n")
            self.assertEqual(out.getvalue(), "application message\nnext run\n")
            self.assertEqual(err.getvalue(), "application error\nlate shutdown message\n")

    def test_existing_and_new_logging_handlers_are_routed_and_restored(self):
        logger = logging.getLogger("ytrss-log-fixture")
        old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
        logger.handlers, logger.level, logger.propagate = [], logging.INFO, False
        self.addCleanup(lambda: setattr(logger, "handlers", old_handlers))
        self.addCleanup(lambda: setattr(logger, "level", old_level))
        self.addCleanup(lambda: setattr(logger, "propagate", old_propagate))
        with TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as console:
            path = Path(directory, "ytrss.log")
            existing = logging.StreamHandler(sys.stderr)
            self.addCleanup(existing.close)
            logger.addHandler(existing)
            with start.application_log(path):
                logger.error("preconfigured handler")
                logger.removeHandler(existing)
                created = logging.StreamHandler(sys.stderr)
                self.addCleanup(created.close)
                logger.addHandler(created)
                logger.warning("handler created during startup")
                logger.addHandler(existing)
            self.assertIs(existing.stream, console)
            self.assertIs(created.stream, console)
            logger.removeHandler(existing)
            logger.error("after log closed")
            self.assertEqual(path.read_text(),
                             "preconfigured handler\nhandler created during startup\n")
            self.assertEqual(console.getvalue(), path.read_text() + "after log closed\n")

    def test_concurrent_stdout_and_stderr_writes_are_not_lost(self):
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = Path(directory, "ytrss.log")
            ready = Barrier(8)

            def write(index):
                ready.wait(timeout=5)
                stream = sys.stdout if index % 2 == 0 else sys.stderr
                for number in range(200):
                    stream.write(f"потік {index}, повідомлення {number}\n")

            with start.application_log(path):
                threads = [Thread(target=write, args=(index,)) for index in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
            self.assertCountEqual(path.read_text().splitlines(), [
                f"потік {index}, повідомлення {number}"
                for index in range(8) for number in range(200)
            ])

    def test_flask_exception_includes_traceback_and_console_output(self):
        app = Flask("ytrss-flask-log-fixture")
        # Flask's default handler uses a dynamic WSGI error stream.
        handler = app.logger.handlers[0]
        original_stream = handler.stream

        @app.get("/failure")
        def failure():
            print("request message")
            print("request stderr", file=sys.stderr)
            raise RuntimeError("request fixture failure")

        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            path = Path(directory, "ytrss.log")
            with start.application_log(path):
                response = app.test_client().get("/failure")
            self.assertEqual(response.status_code, 500)
            log = path.read_text()
            self.assertIn("request message\n", log)
            self.assertIn("request stderr\n", log)
            self.assertIn("Traceback (most recent call last)", log)
            self.assertIn("RuntimeError: request fixture failure", log)
            self.assertIn("request message\n", out.getvalue())
            self.assertIn("RuntimeError: request fixture failure", err.getvalue())
            self.assertIs(handler.stream, original_stream)

    def test_main_startup_failure_is_logged_and_exit_code_is_preserved(self):
        script = Path(start.__file__).resolve()
        with TemporaryDirectory() as directory:
            path = Path(directory, "ytrss.log")
            path.write_text("previous run\n")
            Path(directory, "ytrss_config.json").write_text("{invalid json")
            result = subprocess.run(
                [sys.executable, str(script)], cwd=directory,
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 1)
            log = path.read_text()
            self.assertTrue(log.startswith("previous run\n"))
            self.assertIn("JSONDecodeError", log)
            self.assertEqual(log.count("Traceback (most recent call last)"), 1)
            self.assertIn("JSONDecodeError", result.stderr)
            self.assertEqual(result.stderr.count("Traceback (most recent call last)"), 1)


if __name__ == "__main__":
    unittest.main()
