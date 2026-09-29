from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event
import unittest
from unittest.mock import patch

from config import ConfigStore
from transcription_queue import AUTO_RSS, AUTO_YOUTUBE, TranscriptionQueues


class ConfigTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.json"
        self.store = ConfigStore(self.path)

    def test_snapshots_and_completed_edits_do_not_expose_shared_containers(self):
        snapshot = self.store.snapshot()
        snapshot["channel_subscriptions"].append("uncommitted")
        snapshot["transcription-prompts"]["en"][0] = "uncommitted"
        self.assertEqual(self.store.snapshot()["channel_subscriptions"], [])
        self.assertNotEqual(self.store.snapshot()["transcription-prompts"]["en"][0], "uncommitted")
        with self.store.edit() as cfg:
            cfg["channel_names_dict"]["one"] = "First"
        cfg["channel_names_dict"]["one"] = "Changed outside lock"
        self.assertEqual(self.store.snapshot()["channel_names_dict"], {"one": "First"})
        self.assertEqual(json.loads(self.path.read_text())["channel_names_dict"], {"one": "First"})

    def test_defaults_external_reload_and_unknown_keys_are_preserved(self):
        self.path.write_text(json.dumps({"title": "Initial", "custom": [1]}))
        with patch("config.json.load", wraps=json.load) as load:
            self.assertEqual(self.store.snapshot()["title"], "Initial")
            self.assertEqual(self.store.snapshot()["port"], 5000)
            self.assertEqual(load.call_count, 1)
        self.path.write_text(json.dumps({"title": "Changed externally", "custom": [1, 2]}))
        with self.store.edit() as cfg:
            cfg["max_days"] = 90
        persisted = json.loads(self.path.read_text())
        self.assertEqual(persisted["title"], "Changed externally")
        self.assertEqual(persisted["custom"], [1, 2])
        self.assertEqual(persisted["max_days"], 90)

    def test_failed_edit_or_save_preserves_memory_and_complete_json(self):
        self.store.save()
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            with self.store.edit() as cfg:
                cfg["title"] = "Must roll back"
                raise ValueError("invalid input")
        with patch("config.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                with self.store.edit() as cfg:
                    cfg["title"] = "Must also roll back"
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.snapshot()["title"], "YTRSS feed")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_concurrent_nested_edits_and_unlocked_disk_readers(self):
        self.store.save()
        ready = Barrier(6)
        finished = Event()

        def write(worker):
            ready.wait(timeout=5)
            for number in range(30):
                key = f"{worker}:{number}"
                with self.store.edit() as cfg:
                    cfg["channel_subscriptions"].append(key)
                    cfg["channel_names_dict"][key] = key

        def read():
            ready.wait(timeout=5)
            while not finished.is_set():
                for cfg in (self.store.snapshot(), json.loads(self.path.read_text())):
                    self.assertEqual(set(cfg["channel_subscriptions"]), set(cfg["channel_names_dict"]))
                finished.wait(0.001)

        with ThreadPoolExecutor(max_workers=6) as executor:
            writers = [executor.submit(write, worker) for worker in range(5)]
            reader = executor.submit(read)
            try:
                for future in writers:
                    future.result(timeout=15)
            finally:
                finished.set()
            reader.result(timeout=5)
        expected = {f"{worker}:{number}" for worker in range(5) for number in range(30)}
        self.assertEqual(set(self.store.snapshot()["channel_subscriptions"]), expected)
        self.assertEqual(len(self.store.snapshot()["channel_subscriptions"]), len(expected))


class QueueTests(unittest.TestCase):
    def test_fifo_deduplication_and_independent_queues(self):
        queues = TranscriptionQueues()
        self.assertTrue(queues.enqueue(AUTO_YOUTUBE, "one"))
        self.assertFalse(queues.enqueue(AUTO_YOUTUBE, "one"))
        queues.enqueue(AUTO_YOUTUBE, "two")
        queues.enqueue(AUTO_RSS, "one")
        queues.enqueue("srt", "manual")
        self.assertEqual(queues.drain([AUTO_YOUTUBE]), {AUTO_YOUTUBE: ["one", "two"]})
        self.assertEqual(queues.drain([AUTO_RSS, "srt"]), {AUTO_RSS: ["one"], "srt": ["manual"]})
        self.assertTrue(queues.enqueue(AUTO_YOUTUBE, "one"))

    def test_concurrent_producers_and_draining_do_not_lose_jobs(self):
        queues = TranscriptionQueues()
        ready = Barrier(5)
        finished = Event()

        def produce(worker):
            ready.wait(timeout=5)
            for number in range(400):
                queues.enqueue(AUTO_YOUTUBE, f"{worker}:{number}")

        def consume():
            ready.wait(timeout=5)
            received = []
            while not finished.is_set():
                received.extend(queues.drain([AUTO_YOUTUBE])[AUTO_YOUTUBE])
                finished.wait(0.001)
            received.extend(queues.drain([AUTO_YOUTUBE])[AUTO_YOUTUBE])
            return received

        with ThreadPoolExecutor(max_workers=5) as executor:
            producers = [executor.submit(produce, worker) for worker in range(4)]
            consumer = executor.submit(consume)
            try:
                for future in producers:
                    future.result(timeout=5)
            finally:
                finished.set()
            received = consumer.result(timeout=5)
        expected = [f"{worker}:{number}" for worker in range(4) for number in range(400)]
        self.assertEqual(Counter(received), Counter(expected))

    def test_concurrent_duplicate_requests_prepare_output_only_once(self):
        queues = TranscriptionQueues()
        prepared = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(
                lambda _: queues.enqueue("srt", "one", prepare=lambda: prepared.append("clean")),
                range(100),
            ))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(prepared, ["clean"])

    def test_job_is_published_after_preparation_and_failure_does_not_publish(self):
        queues = TranscriptionQueues()
        with self.assertRaises(OSError):
            queues.enqueue("srt", "failed", prepare=lambda: (_ for _ in ()).throw(OSError()))
        self.assertEqual(queues.drain(["srt"]), {"srt": []})
        preparing = Event()
        release = Event()

        def prepare():
            preparing.set()
            self.assertTrue(release.wait(5))

        with ThreadPoolExecutor(max_workers=2) as executor:
            producer = executor.submit(queues.enqueue, "srt", "one", prepare)
            self.assertTrue(preparing.wait(5))
            consumer = executor.submit(queues.drain, ["srt"])
            try:
                self.assertFalse(consumer.done())
            finally:
                release.set()
            self.assertTrue(producer.result(timeout=5))
            self.assertEqual(consumer.result(timeout=5), {"srt": ["one"]})


if __name__ == "__main__":
    unittest.main()
