from concurrent.futures import ThreadPoolExecutor
from contextlib import chdir, ExitStack
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch
from xml.etree import ElementTree
from lxml import etree

import config
import utils
import ytrss
import ytrss_transcribe as transcriber
import ytrss_upd as updater
from transcription_queue import AUTO_RSS, AUTO_YOUTUBE, TranscriptionQueues


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        directory = self.context.enter_context(TemporaryDirectory())
        self.context.enter_context(chdir(directory))
        Path("yt-video").mkdir()
        self.store = config.ConfigStore("ytrss_config.json")
        self.context.enter_context(patch.object(config, "_store", self.store))
        self.queues = TranscriptionQueues()
        for module in (ytrss, updater, transcriber):
            self.context.enter_context(patch.object(module, "transcription_queues", self.queues))
        self.context.enter_context(patch.dict(ytrss.app.config, {"TESTING": True}))
        self.client = ytrss.app.test_client()

    def test_subscription_and_toggle_routes_preserve_their_behavior(self):
        with patch.object(ytrss, "get_rss_name", return_value="Fixture podcast"):
            for endpoint, field, value, setting in (
                ("channel", "source_id", "channel-id", "channel_subscriptions"),
                ("playlist", "source_id", "playlist-id", "playlist_subscriptions"),
                ("rss", "rss_link", "https://fixture.invalid/feed", "rss_subscriptions"),
            ):
                for _ in range(2):
                    self.assertEqual(self.client.post(f"/subscribe/{endpoint}", data={field: value}).status_code, 302)
                self.assertEqual(config.get_config()[setting], [value])
                self.client.post(f"/unsubscribe/{endpoint}", data={field: value})
                self.assertEqual(config.get_config()[setting], [])
        for endpoint, setting in (
            ("downloading", "sources_with_disabled_downloading"),
            ("auto-transcription", "sources_with_disabled_auto_transcription"),
        ):
            for _ in range(2):
                self.client.post(f"/{endpoint}/disable", data={"source_id": "source"})
            self.assertEqual(config.get_config()[setting], ["source"])
            self.client.post(f"/{endpoint}/enable", data={"source_id": "source"})
            self.assertEqual(config.get_config()[setting], [])
        self.assertEqual(json.loads(Path("ytrss_config.json").read_text()), config.get_config())

    def test_concurrent_http_settings_changes_do_not_overwrite_each_other(self):
        def subscribe(number):
            with ytrss.app.test_client() as client:
                response = client.post("/subscribe/channel", data={"source_id": str(number)})
                self.assertEqual(response.status_code, 302)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(subscribe, list(range(40)) * 2))
        self.assertEqual(set(config.get_config()["channel_subscriptions"]), {str(i) for i in range(40)})
        self.assertEqual(len(config.get_config()["channel_subscriptions"]), 40)

    def test_settings_forms_commit_together_and_invalid_form_rolls_back(self):
        self.client.post("/download-cfg", data={
            "max_days": "60", "deliver_days": "7", "duplicate_detection_threshold": "91",
        })
        self.client.post("/auto-transcription-cfg", data={
            "default_engine": "srt", "auto_transcript_engine_rss": "srt",
            "auto_transcript_hours": "24", "wait_for_download_hours": "5",
            "duplicate_detection_threshold_transcription": "92",
        })
        cfg = config.get_config()
        self.assertEqual((cfg["max_days"], cfg["deliver_days"], cfg["auto_transcript_hours"]), (60, 7, 24))
        with self.assertRaises(ValueError):
            self.client.post("/download-cfg", data={
                "max_days": "70", "deliver_days": "invalid", "duplicate_detection_threshold": "93",
            })
        self.assertEqual(config.get_config(), cfg)

    def test_name_cache_merge_does_not_lose_concurrent_results(self):
        def response(url, **kwargs):
            source = url.rsplit("=", 1)[-1]
            return SimpleNamespace(status_code=200, text=(
                '<feed xmlns="http://www.w3.org/2005/Atom"><author>'
                f'<name>{source}</name></author></feed>'
            ))

        with patch.object(utils, "sleep"), patch.object(utils.requests, "get", side_effect=response):
            with ThreadPoolExecutor(max_workers=8) as executor:
                names = list(executor.map(utils.get_channel_name, [str(i) for i in range(30)]))
        self.assertEqual(names, [str(i) for i in range(30)])
        self.assertEqual(config.get_config()["channel_names_dict"], {str(i): str(i) for i in range(30)})

    def test_manual_queue_cleans_old_output_and_suppresses_repeated_request(self):
        Path("yt-video/episode.txt").write_text("old transcription")
        Path("yt-video/episode.log").write_text("old error")
        self.assertIn(b"Scheduled", self.client.get("/transcribe/srt/episode").data)
        self.assertFalse(Path("yt-video/episode.txt").exists())
        self.assertFalse(Path("yt-video/episode.log").exists())
        self.assertIn(b"already scheduled", self.client.get("/transcribe/srt/episode").data)
        self.assertIn(b"Unknown transcription engine", self.client.get("/transcribe/unknown/episode").data)
        self.assertEqual(self.queues.drain(["srt"]), {"srt": ["episode"]})
        self.assertFalse(list(Path(".").glob("*.txt")))

    def test_batch_order_existing_transcripts_and_arrivals_during_processing(self):
        with config.edit_config() as cfg:
            cfg["gemini_api_key"] = "fixture-key"
            cfg["auto_transcript_engine"] = "srt"
            cfg["auto_transcript_engine_rss"] = "gemini_s"
        for queue, filename in (
            ("gemini_s", "manual-rss"), ("srt", "manual-video"),
            (AUTO_YOUTUBE, "auto-video"), (AUTO_RSS, "auto-rss"),
            ("srt", "existing"),
        ):
            self.queues.enqueue(queue, filename)
        Path("yt-video/existing.txt").write_text("Keep existing output")
        Path("yt-video/temporary.mp3").write_bytes(b"fixture")

        def transcribe(filename, engine):
            if filename == "manual-rss":
                self.queues.enqueue("srt", "arrived-later")

        with patch.object(transcriber, "transcribe_video", side_effect=transcribe) as process:
            transcriber.run_transcription()
        self.assertEqual(process.call_args_list, [
            call("manual-rss", engine="gemini_s"), call("auto-rss", engine="gemini_s"),
            call("manual-video", engine="srt"), call("auto-video", engine="srt"),
        ])
        self.assertEqual(self.queues.drain(["srt"]), {"srt": ["arrived-later"]})
        self.assertFalse(Path("yt-video/temporary.mp3").exists())
        self.assertEqual(Path("yt-video/existing.txt").read_text(), "Keep existing output")

    def test_automatic_engine_is_selected_when_consuming_and_disabled_manual_queue_survives(self):
        self.queues.enqueue(AUTO_YOUTUBE, "video")
        self.queues.enqueue(AUTO_RSS, "rss")
        self.queues.enqueue("claude_t", "manual-disabled")
        with config.edit_config() as cfg:
            cfg["gemini_api_key"] = "fixture-key"
            cfg["auto_transcript_engine"] = "gemini_t"
            cfg["auto_transcript_engine_rss"] = "unavailable"
        with patch.object(transcriber, "transcribe_video") as process:
            transcriber.run_transcription()
        process.assert_called_once_with("video", engine="gemini_t")
        self.assertEqual(self.queues.drain([AUTO_RSS, "claude_t"]), {
            AUTO_RSS: [], "claude_t": ["manual-disabled"],
        })

    def test_real_subtitle_transcription_and_http_feed_use_the_episode_argument(self):
        Path("yt-video/episode.desc").write_text(
            '<entry><title>Fixture episode</title><summary>Details</summary>'
            '<link href="https://www.youtube.com/watch?v=fixture"/>'
            '<auto_transcription>true</auto_transcription></entry>'
        )
        Path("yt-video/episode.srt").write_text("First sentence. Second sentence.")
        self.queues.enqueue("srt", "episode")
        with patch.object(transcriber, "find_duplicate_episode", return_value=None) as duplicate:
            transcriber.run_transcription()
        self.assertEqual(duplicate.call_args.args[0]["id"], "episode")
        self.assertIn("First sentence", Path("yt-video/episode.txt").read_text())
        response = self.client.get("/feed")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ElementTree.fromstring(response.data).find("channel/item/title").text, "Fixture episode")
        self.assertIn(b"First sentence", self.client.get("/read").data)

    def test_youtube_and_rss_fixture_feeds_schedule_in_memory_after_metadata_is_ready(self):
        now = datetime.now(timezone.utc) - timedelta(hours=1)
        youtube = f'''<feed xmlns="http://www.w3.org/2005/Atom"
            xmlns:media="http://search.yahoo.com/mrss/">
            <title>Fixture channel</title><entry><title>Video</title>
            <link href="https://www.youtube.com/watch?v=fixture"/>
            <published>{now.isoformat()}</published>
            <media:group><media:description>Video description</media:description></media:group>
            </entry></feed>'''
        rss = f'''<rss><channel><title>Fixture podcast</title><item><title>Audio</title>
            <link>https://fixture.invalid/episode</link><pubDate>{now.isoformat()}</pubDate>
            <description>Audio description</description>
            <enclosure url="https://fixture.invalid/audio.mp3" type="audio/mpeg" length="100"/>
            </item></channel></rss>'''
        with config.edit_config() as cfg:
            cfg["sources_with_disabled_downloading"] = ["channel"]
        published_mtimes = {}
        enqueue = self.queues.enqueue

        def record_publication(queue, filename):
            published_mtimes[filename] = Path("yt-video", filename + ".desc").stat().st_mtime
            return enqueue(queue, filename)

        with patch.object(updater, "secure_wait"), \
             patch.object(self.queues, "enqueue", side_effect=record_publication), \
             patch.object(updater, "get_live_status", return_value="not_live"), \
             patch.object(updater, "get_duration", return_value=120), \
             patch.object(updater, "find_duplicate_episode", return_value=None), \
             patch.object(updater.requests, "get", side_effect=[
                 SimpleNamespace(status_code=200, content=youtube.encode()),
                 SimpleNamespace(status_code=200, content=rss.encode()),
             ]):
            updater.parce_yt_item("https://www.youtube.com/feeds/videos.xml?channel_id=channel")
            updater.parce_rss_item("https://fixture.invalid/feed")
        batch = self.queues.drain([AUTO_YOUTUBE, AUTO_RSS])
        self.assertEqual(len(batch[AUTO_YOUTUBE]), 1)
        self.assertEqual(len(batch[AUTO_RSS]), 1)
        for filename in batch[AUTO_YOUTUBE] + batch[AUTO_RSS]:
            metadata = Path("yt-video", filename + ".desc")
            entry = etree.parse(metadata, etree.XMLParser(recover=True))
            self.assertEqual(entry.find("auto_transcription").text, "true")
            self.assertEqual(entry.find("published").text, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            self.assertEqual(metadata.stat().st_mtime, published_mtimes[filename])
        self.assertFalse(list(Path(".").glob("*.txt")))

    def test_modules_import_in_either_order(self):
        source = str(Path(ytrss.__file__).parent)
        for modules in ("utils, ytrss_transcribe, ytrss_upd, ytrss", "ytrss_transcribe, utils, ytrss"):
            result = subprocess.run([
                sys.executable, "-c",
                f"import sys; sys.path.insert(0, {source!r}); import {modules}",
            ], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
