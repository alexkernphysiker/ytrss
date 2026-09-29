from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest

import start


class WorkerLogTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        directory = self.context.enter_context(TemporaryDirectory())
        self.log = Path(directory, "worker.log")
        self.context.enter_context(redirect_stdout(start.ThreadOutput(io.StringIO())))
        self.context.enter_context(redirect_stderr(start.ThreadOutput(io.StringIO())))

    def test_passes_append_and_empty_pass_preserves_previous_output(self):
        self.log.write_text("previous application run\n")
        with start.worker_log(self.log):
            print("first pass")
        with start.worker_log(self.log):
            print("second pass", file=sys.stderr)
        with start.worker_log(self.log):
            pass
        self.assertEqual(self.log.read_text(), "previous application run\nfirst pass\nsecond pass\n")

    def test_exact_limit_is_not_rewritten(self):
        original = b"existing line\n" * start.LOG_MAX_LINES
        self.log.write_bytes(original)
        os.utime(self.log, (1_000_000_000, 1_000_000_000))
        before = self.log.stat()
        with start.worker_log(self.log):
            pass
        self.assertEqual(self.log.read_bytes(), original)
        self.assertEqual(self.log.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.log.stat().st_ino, before.st_ino)

    def test_oversized_existing_log_keeps_last_lines_without_decoding(self):
        lines = [f"Рядок {number}".encode() + b"\xff\r\n" for number in range(20_002)]
        lines.append(b"last incomplete line")
        self.log.write_bytes(b"".join(lines))
        with start.worker_log(self.log):
            self.assertEqual(self.log.read_bytes(), b"".join(lines[-20_000:]))
        self.assertEqual(self.log.read_bytes(), b"".join(lines[-20_000:]))

    def test_next_pass_trims_previous_output_before_appending(self):
        with start.worker_log(self.log):
            sys.stdout.write("".join(f"line {number}\n" for number in range(20_005)))
        self.assertEqual(len(self.log.read_text().splitlines()), 20_005)
        with start.worker_log(self.log):
            self.assertEqual(self.log.read_text().splitlines(), [f"line {number}" for number in range(5, 20_005)])
            print("next pass")
        self.assertEqual(len(self.log.read_text().splitlines()), 20_001)
        with start.worker_log(self.log):
            pass
        self.assertEqual(self.log.read_text().splitlines(), [f"line {number}" for number in range(6, 20_005)] + ["next pass"])

    def test_subprocess_stdout_and_stderr_are_included_in_line_limit(self):
        self.log.write_text("old log\n")
        with start.worker_log(self.log):
            result = subprocess.call(
                [sys.executable, "-u", "-c",
                 "import sys; sys.stdout.write(''.join(f'child {i}\\n' for i in range(20005))); "
                 "sys.stderr.write('child error\\n')"],
                stdout=sys.stdout, stderr=sys.stderr, timeout=10,
            )
        self.assertEqual(result, 0)
        self.assertEqual(len(self.log.read_text().splitlines()), 20_007)
        with start.worker_log(self.log):
            pass
        self.assertEqual(self.log.read_text().splitlines(),
                         [f"child {number}" for number in range(6, 20_005)] + ["child error"])

    def test_next_pass_retains_recent_history_and_previous_traceback(self):
        self.log.write_text("".join(f"old {number}\n" for number in range(20_000)))
        stop = Event()

        def fail():
            stop.set()
            print("failed pass output")
            raise RuntimeError("fixture failure")

        start.run_periodically(fail, 60, stop, self.log)
        self.assertGreater(len(self.log.read_text().splitlines()), 20_000)
        with start.worker_log(self.log):
            pass
        lines = self.log.read_text().splitlines()
        self.assertEqual(len(lines), 20_000)
        self.assertNotIn("old 0", lines)
        self.assertIn("old 19999", lines)
        self.assertIn("failed pass output", lines)
        self.assertEqual(lines[-1], "RuntimeError: fixture failure")


if __name__ == "__main__":
    unittest.main()
