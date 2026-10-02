import json
import os
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

def default_config():
    return {
        "host" : "127.0.0.1",
        "port" : 5000,
        "url_link" : "http://127.0.0.1:5000",
        "max_days" : 30,
        "deliver_days" : 3,
        "auto_transcript_engine" : "srt",
        "auto_transcript_engine_rss": "gemini_s",
        "auto_transcript_hours" : 12,
        "wait_for_download_hours" : 3,
        "channel_subscriptions" : [],
        "playlist_subscriptions" : [],
        "rss_subscriptions": [],
        "sources_with_disabled_auto_transcription" : [],
        "sources_with_disabled_downloading" : [],
        "channel_names_dict": {},
        "playlist_names_dict": {},
        "rss_names_dict": {},
        "google_search_api_key": "",
        "gemini_api_key": "",
        "gemini_model": "gemini-3.8-flash",
        "openai_api_key": "",
        "open_ai_text_model": "gpt-5.6",
        "open_ai_audio_model": "gpt-4o-transcribe-diarize",
        "claude_api_key": "",
        "claude_model": "claude-opus-5",
        "yt-dlp-enabled": True,
        "yt-dlp-options": "",
        "yt-dlp-options-rss-podcasts": "",
        "yt-dlp-formats": ["-S res:480", "-S res:360", "-S res:240", "-x"],
        "proxies-youtube": {},
        "proxies-rss": {},
        "delay-between-fetches": 1,
        "re-transcription": False,
        "transcription-prompts": {
            "en": [
                "Please make a text transcription with splitting the text into paragraphs and chapters.",
                "Please summarize.",
                "Start your response with the requested text without explainations how it was obtained.",
                "Please obtain the full text of the article available on this page."
            ],
            "uk": [
                "Будь ласка, зроби текстову транскрипцію з логічним розбиттям на абзаци та розділи.",
                "Напиши стислий переказ.",
                "Починай відповідь відразу з тексту без пояснень, як ти його отримав.",
                "Будь ласка, напиши повний текст статті доступної на цій сторінці."
            ]
        },
        "default_language": "en",
        "duplicate_detection_threshold": 100,
        "duplicate_detection_threshold_transcription": 100,
        "headers" : {
            "User-Agent": "ytrss/0.1 (+https://github.com/alexkernphysiker/ytrss)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8"
        },
        "title": "YTRSS feed",
        "temporary_block_yt_download": False,
        "summarize_min_length": 7168
    }

class ConfigStore:
    """One shared configuration, with isolated reads and serialized edits."""

    def __init__(self, path="ytrss_config.json"):
        self.path = Path(path)
        self._lock = RLock()
        self._config = default_config()
        self._signature = None

    @staticmethod
    def _signature_for(stat):
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _reload(self):
        # Keep support for edits made directly in the JSON file. Unchanged
        # settings are served from memory rather than parsed on every access.
        try:
            signature = self._signature_for(self.path.stat())
        except FileNotFoundError:
            self._signature = None
            return
        if signature != self._signature:
            with self.path.open(encoding="utf-8") as stream:
                signature = self._signature_for(os.fstat(stream.fileno()))
                settings = json.load(stream)
            self._config.update(settings)
            self._signature = signature

    def _write(self, settings):
        temporary_path = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(settings, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
                signature = self._signature_for(os.fstat(stream.fileno()))
            os.replace(temporary_path, self.path)
            self._signature = signature
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def snapshot(self):
        with self._lock:
            self._reload()
            return deepcopy(self._config)

    @contextmanager
    def edit(self):
        """Commit a short read/modify/write transaction, or roll it back."""
        with self._lock:
            self._reload()
            settings = deepcopy(self._config)
            yield settings
            if settings != self._config:
                self._write(settings)
                # Do not let a caller retain a mutable reference to our state.
                self._config = deepcopy(settings)

    def save(self):
        with self._lock:
            self._reload()
            self._write(self._config)


_store = ConfigStore()


def get_config():
    """Return an independent snapshot; use edit_config() to change settings."""
    return _store.snapshot()


def edit_config():
    """Lock, edit and persist settings together, including nested containers."""
    return _store.edit()


def save_config():
    """Persist the shared settings (also creates the initial config file)."""
    _store.save()
