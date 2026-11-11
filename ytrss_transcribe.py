import os
import subprocess
import shlex
import sys
from html import escape
from pathlib import Path
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from lxml import etree, html as lxml_html
from extract_page import fetch_readable_article, looks_like_full_article
from repeatings_detector import find_duplicate_episode, mark_episode_as_duplicate
from datetime import datetime, timedelta, timezone
from utils import secure_wait, filter_subs
from config import get_config
from lang_detect import *
from transcription_queue import AUTO_RSS, AUTO_YOUTUBE, transcription_queues
import json
import re
import math
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import RLock
from time import monotonic
from urllib.parse import urlsplit
from utils import clean_subtitles
from classify_news import save_news_vector

def get_engine_map(cfg=None):
    if cfg is None:
        cfg = get_config()
    res = dict()
    if cfg["thinking"]["gemini"]["api_key"] != "":
        res["gemini_s"] = "Gemini"
    if cfg["thinking"]["openai"]["api_key"] != "":
        res["openai_s"] = "OpenAI"
    if cfg["thinking"]["claude"]["api_key"] != "":
        res["claude_s"] = "Claude"
    res["srt"] = "Just download subtitles from YT"
    return res

TEXT_CHUNK_LENGTH = 0
SUSPICIOUS_EMPTY_AUDIO_SECONDS = 30.0
_TRACE_CONTEXT = ContextVar("transcription_trace", default=None)
_RETRY_STATE = {}
_POSTPROCESS_PENDING = set()
_TEMPORARY_AUDIO = {}
_RETRY_LOCK = RLock()


class TranscriptionPending(ValueError):
    """Source is not ready yet; keep the job for a later pass."""


class TranscriptionDuplicate(Exception):
    """The raw text already belongs to another visible material."""

    def __init__(self, duplicate_of):
        self.duplicate_of = duplicate_of
        super().__init__("Duplicate raw transcript")


@contextmanager
def _trace_stage(**fields):
    context = {**(_TRACE_CONTEXT.get() or {}), **fields}
    token = _TRACE_CONTEXT.set(context)
    try:
        yield
    finally:
        _TRACE_CONTEXT.reset(token)


def _trace(event, **fields):
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    details = {**context, **fields}
    filename = details.pop("filename", None)
    level = details.pop("level", "INFO")
    payload = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{timestamp} {level} Transcription {filename or '-'} {event} {payload}", flush=True)
    if filename:
        try:
            write_log(filename, f"{level} {event} {payload}")
        except OSError as error:
            print(f"{timestamp} WARNING Transcription {filename} log.write_failed "
                  f"{type(error).__name__}", flush=True)


def _usage_values(usage):
    # Only numeric counters; never dump SDK responses, prompts or credentials.
    paths = {
        "input_tokens": ("prompt_token_count", "input_tokens", "total_input_tokens"),
        "output_tokens": ("candidates_token_count", "output_tokens", "total_output_tokens"),
        "thinking_tokens": ("thoughts_token_count", "output_tokens_details.reasoning_tokens",
                            "total_thought_tokens"),
        "total_tokens": ("total_token_count", "total_tokens"),
        "cached_input_tokens": ("cached_content_token_count", "input_tokens_details.cached_tokens",
                                "cache_read_input_tokens", "total_cached_tokens"),
        "cache_creation_tokens": ("cache_creation_input_tokens",),
        "audio_seconds": ("seconds",),
    }
    result = {}
    for label, alternatives in paths.items():
        for path in alternatives:
            value = usage
            for field in path.split("."):
                value = value.get(field) if isinstance(value, dict) else getattr(value, field, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[label] = value
                break
    return result


def _model_request(call, parse):
    context = _TRACE_CONTEXT.get() or {}
    prefix = "audio.part" if context.get("stage") == "audio_transcription" else "request"
    started = monotonic()
    _trace(prefix + ".start")
    try:
        text = parse(call())
    except Exception as error:
        _trace(prefix + ".failed", level="ERROR", error_type=type(error).__name__,
               elapsed_seconds=round(monotonic() - started, 3))
        raise
    _trace(prefix + ".completed", technical_validation="passed",
           elapsed_seconds=round(monotonic() - started, 3), output_chars=len(text))
    return text


def _log_empty_audio(text):
    context = _TRACE_CONTEXT.get() or {}
    if context.get("stage") != "audio_transcription":
        return
    if isinstance(text, str) and any(character.isalnum() for character in text):
        return
    duration = context.get("audio_seconds")
    suspicious = duration >= SUSPICIOUS_EMPTY_AUDIO_SECONDS if duration is not None else None
    _trace("audio.part.suspicious_empty" if suspicious else "audio.part.empty",
           level="WARNING", suspicious=suspicious,
           empty_audio_threshold_seconds=SUSPICIOUS_EMPTY_AUDIO_SECONDS)


def _logged_audio_duration(path):
    try:
        return _audio_duration(path)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        _trace("audio.duration.unavailable", level="WARNING", error_type=type(error).__name__)
        return None


def _description_audio_duration(filename):
    path = Path("yt-video", filename + ".desc")
    try:
        raw = etree.parse(path).findtext("duration")
        values = [float(value) for value in raw.split(":")]
        duration = 0.0
        for value in values:
            duration = duration * 60 + value
        return duration if math.isfinite(duration) and duration > 0 else None
    except (OSError, ValueError, TypeError, AttributeError, etree.XMLSyntaxError):
        return None


def _openai_audio_response_text(response):
    segments = list(getattr(response, "segments", None) or [])
    texts = [getattr(segment, "text", None) for segment in segments]
    _trace("response", provider="openai", endpoint="audio.transcriptions",
           completion_status="not_provided_by_endpoint", segments=len(segments),
           usage=_usage_values(getattr(response, "usage", None)))
    _log_empty_audio("".join(text for text in texts if isinstance(text, str)))
    speech = []
    previous = None
    for segment in segments:
        words = _require_text(segment.text, "audio segment")
        speech.append(("\n- " if segment.speaker != previous else " ") + words)
        previous = segment.speaker
    return _require_text("".join(speech), "audio transcript")


def _claude_response_text(final, output):
    _trace("response", provider="claude", finish_reason=final.stop_reason,
           usage=_usage_values(getattr(final, "usage", None)))
    if final.stop_reason != "end_turn":
        raise ValueError(f"Claude generation did not finish normally: {final.stop_reason}")
    return _require_text(output, "Claude response")


def _gemini_page_response_text(interaction):
    _trace("response", provider="gemini", endpoint="interactions", status=interaction.status,
           usage=_usage_values(getattr(interaction, "usage", None)))
    if interaction.status != "completed":
        raise ValueError(f"Gemini page interaction not completed: {interaction.status}")
    blocks = []
    steps = getattr(interaction, "steps", None) or getattr(interaction, "outputs", [])
    for step in steps:
        if step.type == "text":
            blocks.append(step.text)
        elif step.type == "model_output":
            blocks.extend(block.text for block in step.content if block.type == "text")
    return _require_text("\n".join(blocks), "Gemini page text")


def _require_text(text, stage="transcription"):
    if not isinstance(text, str):
        raise TypeError(f"{stage}: expected text, got {type(text).__name__}.")
    text = text.strip()
    if not text or not any(character.isalnum() for character in text):
        raise ValueError(f"{stage}: empty text.")
    # These are responses to a missing input, not the requested transcription.
    if len(text) < 1000 and re.match(
        r"(?is)^(?:it looks like you (?:didn't|did not) include any text|"
        r"please provide the text (?:you would like|to be)|"
        r"no (?:text|transcript|subtitles) (?:was|were|has been) (?:provided|included))",
        text,
    ):
        raise ValueError(f"{stage}: model reported missing input.")
    return text


def _speech(text):
    return clean_subtitles(text) if isinstance(text, str) else ""


def _usable(text):
    try:
        _require_text(_speech(text), "cached text")
        return True
    except (TypeError, ValueError):
        return False


def _atomic_text(path, text, reference=None):
    text = _require_text(text, "file content")
    path = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if reference is not None and Path(reference).exists():
            modified = Path(reference).stat().st_mtime
            os.utime(temporary, (modified, modified))
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _chunks(text):
    text = _require_text(_speech(text), "source text")
    chunks = list(filter_subs(text, max_length=TEXT_CHUNK_LENGTH))
    if not chunks:
        raise ValueError("No speech chunks.")
    return [_require_text(chunk, "source chunk") for chunk in chunks]


def _map_text(text, request):
    # Publish only once every source chunk has a complete, nonempty result.
    chunks = _chunks(text)
    result = []
    for index, chunk in enumerate(chunks, 1):
        with _trace_stage(text_part=index, text_parts=len(chunks)):
            _trace("text.part.start", input_chars=len(chunk))
            output = _require_text(request(chunk), "processed chunk")
            _trace("text.part.completed", output_chars=len(output))
            result.append(output)
    return "\n\n".join(result)


def _cache_is_valid(path):
    path = Path(path)
    try:
        return path.is_file() and _usable(path.read_text(encoding="utf-8"))
    except (UnicodeError, OSError):
        return False


def has_transcription(filename):
    """A completed transcript must contain usable text, not merely exist."""
    return _cache_is_valid(Path("yt-video", filename + ".txt"))


def _retry_key(filename, engine):
    return (str(Path("yt-video").resolve()), engine, filename)


def prepare_transcription_retry(filename, engine):
    """Reset only invalid subtitle caches and this job's retry delay."""
    for suffix in (".raw_srt", ".srt", ".source.srt"):
        path = Path("yt-video", filename + suffix)
        if path.exists() and not _cache_is_valid(path):
            path.unlink()
    key = _retry_key(filename, engine)
    with _RETRY_LOCK:
        _RETRY_STATE.pop(key, None)
        _POSTPROCESS_PENDING.discard(key)


def _wait_for_youtube(filename):
    path = Path("yt-video", filename + ".desc")
    if get_video_link(path) and datetime.now(timezone.utc) - datetime.fromtimestamp(
            path.stat().st_mtime, timezone.utc) < timedelta(
            hours=get_config()["wait_for_download_hours"]):
        raise TranscriptionPending("Waiting for YouTube subtitles.")


def _probe_audio(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Missing or empty audio file.")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type", "-of", "json", str(path),
    ], check=True, capture_output=True, timeout=30)
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0 or not any(
            stream.get("codec_type") == "audio" for stream in data.get("streams", [])):
        raise ValueError("File has no usable audio.")
    return duration


def _audio_duration(path):
    return _probe_audio(path)


def _valid_audio(path):
    try:
        _probe_audio(path)
        return True
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


def _remember_temporary_audio(path):
    path = Path(path).resolve()
    stat = path.stat()
    _TEMPORARY_AUDIO[path] = (stat.st_dev, stat.st_ino)


def _cleanup_temporary_audio(filename):
    path = Path("yt-video", filename + ".mp3").resolve()
    identity = _TEMPORARY_AUDIO.get(path)
    if identity is None:
        return
    try:
        stat = path.stat()
        # Never remove a pre-existing file or a replacement made by another writer.
        if (stat.st_dev, stat.st_ino) == identity:
            path.unlink()
            print(f"Removed temporary audio file: {path}")
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"Temporary audio cleanup failed for {filename}: {type(error).__name__}: {error}")
        return
    _TEMPORARY_AUDIO.pop(path, None)


def download_subtitles(filename):
    description_path = Path("yt-video", filename + ".desc")
    srt_path = Path("yt-video", filename + ".srt")
    raw_path = Path("yt-video", filename + ".raw_srt")
    original_path = Path("yt-video", filename + ".source.srt")
    for path in (srt_path, raw_path, original_path):
        if path.exists():
            content = _speech(path.read_text(encoding="utf-8"))
            if _usable(content):
                # An existing .srt is readable text; raw/legacy sources need formatting.
                _trace("subtitles.cache_hit", cache=path.suffix, input_chars=len(content))
                return content, path != srt_path
            path.unlink()
    link = get_video_link(description_path)
    if not link or not get_config().get("yt-dlp-enabled"):
        return "", False
    lang = detect_language(description_path) or "en"
    _trace("subtitles.download.start", language=lang)
    secure_wait()
    with TemporaryDirectory(prefix=".subtitles-", dir="yt-video") as directory:
        command = [
            "yt-dlp", *shlex.split(get_config().get("yt-dlp-options") or ""),
            "--skip-download", "--write-auto-subs", "--write-subs", "--sub-lang", lang,
            "-o", str(Path(directory, "captions.%(ext)s")), "--", link,
        ]
        result = subprocess.run(command, capture_output=True, timeout=600)
        if result.returncode != 0:
            # Never promote a destination printed before yt-dlp subsequently failed.
            _trace("subtitles.download.failed", level="WARNING", exit_code=result.returncode)
            write_log(filename, f"Subtitle download failed (exit {result.returncode}).")
            return "", False
        for path in sorted(Path(directory).iterdir()):
            if path.suffix.lower() not in (".vtt", ".srt", ".ttml") or not path.is_file():
                continue
            content = _speech(path.read_text(encoding="utf-8"))
            if _usable(content):
                _trace("subtitles.download.completed", input_chars=len(content))
                return save_raw_subtitles(filename, content), True
    return "", False


def save_raw_subtitles(filename, text):
    text = _require_text(text, "raw transcription")
    text = _require_text("\n".join(filter_subs(text, max_length=0)), "raw subtitles")
    _atomic_text(Path("yt-video", filename + ".raw_srt"), text,
                 Path("yt-video", filename + ".desc"))
    return text


def _context_plain_text(value):
    """Keep paragraph boundaries when converting RSS/page HTML into plain text."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        document = lxml_html.fragment_fromstring(value, create_parent="div")
    except (ValueError, TypeError, etree.ParserError):
        return ""
    for element in document.xpath(".//script | .//style | .//noscript"):
        element.drop_tree()
    for element in document.xpath(".//p | .//div | .//li | .//blockquote | "
                                  ".//h1 | .//h2 | .//h3 | .//br"):
        element.tail = "\n" + (element.tail or "")
    return "\n".join(line.strip() for line in document.text_content().splitlines() if line.strip())


def _raw_text_looks_full(text):
    # The existing heuristic expects HTML paragraphs, not a plain transcript.
    paragraphs = "".join(f"<p>{escape(line)}</p>" for line in text.splitlines() if line.strip())
    return looks_like_full_article(paragraphs)


def _supplement_raw_text(filename, text, description=None):
    text = _require_text(text, "raw transcription")
    with _trace_stage(stage="raw_enrichment"):
        raw_full = _raw_text_looks_full(text)
        _trace("raw.enrichment.checked", input_chars=len(text), looks_full=raw_full)
        if raw_full:
            return text
        if description is None:
            _, description = get_video_title_and_description(filename) or ("", "")
        rss_text = _context_plain_text(description)
        rss_full = _raw_text_looks_full(rss_text)
        _trace("raw.enrichment.description", input_chars=len(rss_text), looks_full=rss_full)
        page_text = ""
        page_full = False
        if not rss_full:
            link = get_page_link(filename)
            try:
                url = urlsplit(link or "")
                usable_link = url.scheme in ("http", "https") and bool(url.netloc)
            except ValueError:
                usable_link = False
            if usable_link:
                _trace("raw.enrichment.page.start")
                cfg = get_config()
                try:
                    article = fetch_readable_article(
                        link, headers=cfg.get("headers"), proxies=cfg.get("proxies-rss"))
                    if article:
                        page_text = (article.get("text") or
                                     _context_plain_text(article.get("html"))).strip()
                        page_full = _raw_text_looks_full(page_text)
                    _trace("raw.enrichment.page.completed", input_chars=len(page_text),
                           looks_full=page_full)
                except Exception as error:
                    page_text = ""
                    page_full = False
                    _trace("raw.enrichment.page.failed", level="WARNING",
                           error_type=type(error).__name__)
            else:
                _trace("raw.enrichment.page.skipped", reason="no_http_page_link")
        candidates = ([("RSS description", rss_text)] if rss_full else
                      [("Page text", page_text)] if page_full else
                      [("RSS description", rss_text), ("Page text", page_text)])
        output = text
        added = []
        for label, candidate in candidates:
            normalized = " ".join(candidate.split())
            if not normalized or not any(character.isalnum() for character in candidate):
                continue
            if normalized in " ".join(output.split()):
                _trace("raw.enrichment.duplicate_skipped", source=label)
                continue
            output += f"\n\n[{label}]\n{candidate}"
            added.append(label)
        _trace("raw.enrichment.completed", sources=added, output_chars=len(output),
               added_chars=len(output) - len(text))
        return output


def _check_raw_duplicate(filename, text, persist_raw=True):
    if not _raw_text_looks_full(text):
        return
    with _trace_stage(stage="raw_duplicate"):
        _trace("raw.duplicate.start", input_chars=len(text))
        # The detector prefers .raw_srt and does not need a formatted transcript.
        raw_path = Path("yt-video", filename + ".raw_srt")
        if persist_raw and (not raw_path.is_file() or raw_path.read_text(encoding="utf-8") != text):
            save_raw_subtitles(filename, text)
        title, description = get_video_title_and_description(filename) or ("", "")
        try:
            duplicate = find_duplicate_episode(
                {"id": filename, "title": title, "description": description},
                threshold=get_config()["duplicate_detection_threshold_transcription"])
            _trace("raw.duplicate.completed", duplicate_of=duplicate)
            if duplicate is None:
                return
            mark_episode_as_duplicate(filename, duplicate)
            entry = etree.parse(Path("yt-video", filename + ".desc"))
            if entry.findtext("duplicate") != "true" or entry.findtext("duplicate_of") != duplicate:
                _trace("raw.duplicate.mark_failed", level="WARNING", duplicate_of=duplicate)
                return
        except Exception as error:
            _trace("raw.duplicate.failed", level="WARNING", error_type=type(error).__name__)
            return
        _trace("raw.duplicate.skipped", duplicate_of=duplicate)
        raise TranscriptionDuplicate(duplicate)


def _enrich_raw_subtitles(filename, text, description=None):
    enriched = _supplement_raw_text(filename, text, description)
    if enriched != text:
        enriched = save_raw_subtitles(filename, enriched)
        _trace("raw.enriched.saved", output_chars=len(enriched))
    _check_raw_duplicate(filename, enriched)
    return enriched


def save_subtitles(filename, text):
    text = _require_text(_speech(text) if isinstance(text, str) else text, "subtitles")
    _atomic_text(Path("yt-video", filename + ".srt"), text,
                 Path("yt-video", filename + ".desc"))


def get_video_title_and_description(filename):
    description_path = "yt-video/" + filename + ".desc"
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title = entry.findtext("title") or ""
        descr = entry.findtext("summary") or entry.findtext("description") or ""
        return title, descr

def make_prompt(lang, summarize = False, title = "", description = ""):
            cfg = get_config()
            if lang not in cfg["transcription-prompts"]:
                lang = cfg["default_language"]
            
            if summarize:
                return cfg["transcription-prompts"][lang][1] + \
                        "\n" + \
                        cfg["transcription-prompts"][lang][2] + \
                        "\n" + \
                       (f"Title: \"{title}\". " if title!="" else "") + \
                       (f"Description: \"{description}\". " if description!="" else "")
            else:
                return cfg["transcription-prompts"][lang][0] + \
                        "\n" + \
                        cfg["transcription-prompts"][lang][2] + \
                        "\n" + \
                       (f"Title: \"{title}\". " if title!="" else "") + \
                       (f"Description: \"{description}\". " if description!="" else "")

def make_page_text_prompt(lang, link):
    cfg = get_config()
    if lang not in cfg["transcription-prompts"]:
        lang = cfg["default_language"]
    return cfg["transcription-prompts"][lang][3] + "\n" + \
                      (f"Link: {link}" if link is not None else "")

def convert_video_to_audio(video_file_path):
    target = Path(str(video_file_path) + ".mp3")
    if _valid_audio(target):
        return str(target)
    with TemporaryDirectory(prefix=".audio-", dir=target.parent) as directory:
        temporary = Path(directory, "audio.mp3")
        result = subprocess.call([
            "ffmpeg", "-nostdin", "-y", "-i", os.fspath(video_file_path), "-vn",
            "-ar", "44100", "-ac", "1", "-b:a", "48k", str(temporary),
        ], timeout=1800, stdout=sys.stdout, stderr=sys.stderr)
        if result != 0 or not _valid_audio(temporary):
            return None
        if Path(video_file_path).exists():
            modified = Path(video_file_path).stat().st_mtime
            os.utime(temporary, (modified, modified))
        os.replace(temporary, target)
        _remember_temporary_audio(target)
    return str(target)

def get_enclosure_link(filename):
    description_path = "yt-video/" + filename + ".desc"
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        enclosure_element = entry.find("enclosure")
        if enclosure_element is not None:
            enclosure =  enclosure_element.get("url")
            if enclosure is not None:
                return enclosure
    return None
def get_page_link(filename):
    description_path = "yt-video/" + filename + ".desc"
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        link_element = entry.find("link")
        if link_element is not None:
            link =  link_element.get("href")
            if link is not None:
                return link
    return None


def split_mp3_file(mp3_file_path, chunk_length_s=1000):
    source = os.fspath(mp3_file_path)
    # Let ffmpeg read URLs directly; only local inputs have a preflight duration.
    source_duration = None if urlsplit(source).scheme else _audio_duration(source)
    # Balanced local segments avoid an unprobeable MP3 encoder-padding fragment.
    segment_time = (chunk_length_s if source_duration is None else
                    source_duration / max(1, math.ceil(source_duration / chunk_length_s)))
    with TemporaryDirectory(prefix=".chunks-", dir="yt-video") as directory:
        pattern = str(Path(directory, "chunk.%05d.mp3"))
        result = subprocess.call([
            "ffmpeg", "-nostdin", "-y", "-i", source,
            "-f", "segment", "-segment_time", str(segment_time), "-c", "copy", pattern,
        ], timeout=1800, stdout=sys.stdout, stderr=sys.stderr)
        paths = sorted(Path(directory).glob("chunk.*.mp3"))
        if result != 0 or not paths:
            raise ValueError("Audio segmentation failed or produced no chunks.")
        durations = [_audio_duration(path) for path in paths]
        if source_duration is not None and abs(sum(durations) - source_duration) > max(1.0, source_duration * 0.02):
            raise ValueError("Audio segmentation did not cover the whole source.")
        _trace("audio.parts.prepared", parts=len(paths), audio_seconds=sum(durations))
        for path in paths:
            yield str(path)


def download_audio_file(url, filename):
    if not filename or not url:
        return None
    target = Path("yt-video", filename + ".mp3")
    if _valid_audio(target):
        return str(target)
    secure_wait()
    with TemporaryDirectory(prefix=".audio-", dir="yt-video") as directory:
        temporary = Path(directory, "audio.mp3")
        result = subprocess.call([
            "yt-dlp", *shlex.split(get_config().get("yt-dlp-options-rss-podcasts") or ""),
            "-x", "--audio-format", "mp3", "-o", str(temporary), "--", url,
        ], timeout=1800, stdout=sys.stdout, stderr=sys.stderr)
        if result != 0 or not _valid_audio(temporary):
            return None
        description = Path("yt-video", filename + ".desc")
        if description.exists():
            modified = description.stat().st_mtime
            os.utime(temporary, (modified, modified))
        os.replace(temporary, target)
        _remember_temporary_audio(target)
    return str(target)

def get_video_link(description_path):
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        link_element = entry.find("link")
        if link_element is not None:
            link = link_element.get("href")
            if link and ("youtube.com" in link or "youtu.be" in link):
                return link
    return None

def run_srt(filename):
    text, needs_formatting = download_subtitles(filename)
    text = _require_text(_speech(text), "subtitles")
    if needs_formatting:
        text = _enrich_raw_subtitles(filename, text)
        save_subtitles(filename, text)
    else:
        _check_raw_duplicate(filename, text, persist_raw=False)
    return text


def write_log(filename, message):
    path = Path("yt-video", filename + ".log")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}\n")
    reference = Path("yt-video", filename + ".desc")
    if reference.exists():
        modified = reference.stat().st_mtime
        os.utime(path, (modified, modified))


def _openai_response_text(response):
    status = response.status
    _trace("response", provider="openai", status=status,
           incomplete_reason=getattr(getattr(response, "incomplete_details", None), "reason", None),
           usage=_usage_values(getattr(response, "usage", None)))
    if status != "completed":
        raise ValueError(f"OpenAI response was not completed: {status}")
    return _require_text(response.output_text, "OpenAI response")


def _gemini_response_text(response):
    candidates = response.candidates or []
    reasons = [getattr(candidate.finish_reason, "name", candidate.finish_reason)
               for candidate in candidates]
    feedback = getattr(response, "prompt_feedback", None)
    blocked = getattr(feedback, "block_reason", None)
    _trace("response", provider="gemini", candidates=len(candidates),
           finish_reasons=reasons, block_reason=getattr(blocked, "name", blocked),
           usage=_usage_values(getattr(response, "usage_metadata", None)))
    if not candidates:
        _log_empty_audio(None)
        raise ValueError(f"Gemini returned no candidates; feedback={feedback}")
    try:
        text = response.text
    except (AttributeError, TypeError, ValueError):
        text = None
    _log_empty_audio(text)
    for reason in reasons:
        if reason != "STOP":
            raise ValueError(f"Gemini generation did not finish normally: {reason}")
    return _require_text(text, "Gemini response")


def _translate_if_needed(text, request):
    text = _require_text(text, "translation source")
    language = detect_language_from_text(text)
    if language not in get_config()["transcription-prompts"]:
        target = get_config()["default_language"]
        with _trace_stage(stage="translation"):
            _trace("translation.start", detected_language=language, target_language=target)
            output = _map_text(text, lambda chunk: request(
                f"Translate the supplied text into {target}. Return only the translation.", chunk))
            _trace("translation.completed", output_chars=len(output))
            return output
    _trace("translation.skipped", detected_language=language)
    return text


@dataclass(frozen=True)
class _PageText:
    text: str


def make_verbatim_prompt():
    return (
        "Transcribe all spoken words verbatim, in their spoken order. "
        "Do not summarize, paraphrase, translate, correct wording, omit repetitions, "
        "or add commentary. Return only the complete transcript."
    )


def _transcribe_audio_parts(audio, request):
    """Keep temporary segments alive until every part has been transcribed."""
    parts = []
    chunks = split_mp3_file(audio)
    offset = 0.0
    try:
        for index, chunk in enumerate(chunks, 1):
            duration = _logged_audio_duration(chunk)
            end = offset + duration if offset is not None and duration is not None else None
            with _trace_stage(audio_part=index, audio_seconds=duration,
                              audio_start_seconds=offset, audio_end_seconds=end):
                parts.append(_model_request(
                    lambda: request(chunk),
                    lambda text: _require_text(text, "audio transcript part")))
            offset = end
    finally:
        if hasattr(chunks, "close"):
            chunks.close()
    output = _require_text("\n".join(parts), "audio transcript")
    _trace("audio.all_parts.completed", parts=len(parts), audio_seconds=offset,
           output_chars=len(output))
    return output


class _OpenAIEngine:
    def __init__(self, cfg):
        from openai import OpenAI
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg["thinking"]["openai"]["api_key"])

    def request(self, prompt, chunk):
        _require_text(chunk, "OpenAI input")
        with _trace_stage(provider="openai", model=self.cfg["thinking"]["openai"]["model"],
                          thinking=self.cfg["thinking"]["openai"]["effort"]):
            return _model_request(lambda: self.client.responses.create(
                model=self.cfg["thinking"]["openai"]["model"],
                input=prompt + "\nText:\n" + chunk,
                reasoning={"effort": self.cfg["thinking"]["openai"]["effort"]},
            ), _openai_response_text)

    def transcribe(self, filename, lang):
        _wait_for_youtube(filename)
        video = Path("yt-video", filename)
        audio = convert_video_to_audio(video) if video.exists() else get_enclosure_link(filename)
        if not audio:
            raise ValueError("No usable audio source.")
        with _trace_stage(filename=filename, stage="audio_transcription", provider="openai",
                          model=self.cfg["thinking"]["openai"]["audio_model"]):
            _trace("audio.source.ready", source="local_video" if video.exists() else "podcast",
                   part_seconds_limit=1000)

            def request_part(chunk):
                with open(chunk, "rb") as stream:
                    return _openai_audio_response_text(self.client.audio.transcriptions.create(
                        model=self.cfg["thinking"]["openai"]["audio_model"], file=stream,
                        response_format="diarized_json", chunking_strategy="auto", language=lang,
                    ))

            return _transcribe_audio_parts(audio, request_part)


class _GeminiEngine:
    def __init__(self, cfg):
        from google import genai
        from google.genai import types
        self.cfg = cfg
        self.types = types
        self.client = genai.Client(api_key=cfg["thinking"]["gemini"]["api_key"])
        self.generation_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=cfg["thinking"]["gemini"]["thinking_level"]),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))

    def request(self, prompt, chunk):
        _require_text(chunk, "Gemini input")
        with _trace_stage(provider="gemini", model=self.cfg["thinking"]["gemini"]["model"],
                          thinking=self.cfg["thinking"]["gemini"]["thinking_level"]):
            return _model_request(lambda: self.client.models.generate_content(
                model=self.cfg["thinking"]["gemini"]["model"], contents=[prompt, "Text:\n" + chunk],
                config=self.generation_config), _gemini_response_text)

    def transcribe(self, filename, lang):
        with _trace_stage(filename=filename, stage="audio_transcription", provider="gemini",
                          model=self.cfg["thinking"]["gemini"]["model"],
                          thinking=self.cfg["thinking"]["gemini"]["thinking_level"]):
            youtube = get_video_link(Path("yt-video", filename + ".desc"))
            if youtube:
                _wait_for_youtube(filename)
                duration = _description_audio_duration(filename)
                with _trace_stage(source="youtube", audio_seconds=duration,
                                  audio_end_seconds=duration, audio_start_seconds=0.0,
                                  audio_part=1, audio_parts=1):
                    return _model_request(lambda: self.client.models.generate_content(
                        model=self.cfg["thinking"]["gemini"]["model"], contents=self.types.Content(parts=[
                            self.types.Part.from_uri(file_uri=youtube, mime_type="video/mp4"),
                            self.types.Part.from_text(text=make_verbatim_prompt()),
                        ]), config=self.generation_config), _gemini_response_text)
            enclosure = get_enclosure_link(filename)
            if not enclosure:
                return self.page_text(filename, lang)
            _trace("audio.download.start", source="podcast")
            audio = download_audio_file(enclosure, filename)
            if not audio:
                raise ValueError("Podcast audio download failed.")
            _trace("audio.download.completed", source="podcast",
                   bytes=Path(audio).stat().st_size if Path(audio).is_file() else None)
            with _trace_stage(source="podcast"):
                _trace("audio.source.ready", part_seconds_limit=1000)

                def request_part(chunk):
                    _trace("audio.upload.start")
                    uploaded = self.client.files.upload(file=chunk)
                    _trace("audio.upload.completed")
                    try:
                        return _gemini_response_text(self.client.models.generate_content(
                            model=self.cfg["thinking"]["gemini"]["model"],
                            contents=[uploaded, make_verbatim_prompt()],
                            config=self.generation_config))
                    finally:
                        try:
                            self.client.files.delete(name=uploaded.name)
                            _trace("audio.upload.deleted")
                        except Exception as error:
                            _trace("audio.upload.cleanup_failed", level="WARNING",
                                   error_type=type(error).__name__)

                return _transcribe_audio_parts(audio, request_part)

    def page_text(self, filename, lang):
        with _trace_stage(filename=filename, stage="article", provider="gemini",
                          model=self.cfg["thinking"]["gemini"]["model"],
                          thinking=self.cfg["thinking"]["gemini"]["thinking_level"]):
            output = _model_request(lambda: self.client.interactions.create(
                model=self.cfg["thinking"]["gemini"]["model"],
                input=make_page_text_prompt(lang, get_page_link(filename)),
                tools=[{"type": "url_context"}, {"type": "google_search"}],
                generation_config={"thinking_level": self.cfg["thinking"]["gemini"]["thinking_level"]}),
                _gemini_page_response_text)
            return _PageText(output)


class _ClaudeEngine:
    def __init__(self, cfg):
        import anthropic
        self.cfg = cfg
        self.client = anthropic.Client(api_key=cfg["thinking"]["claude"]["api_key"])

    def request(self, prompt, chunk):
        _require_text(chunk, "Claude input")
        def consume():
            with self.client.messages.stream(
                    model=self.cfg["thinking"]["claude"]["model"], max_tokens=16000,
                    thinking={"type": self.cfg["thinking"]["claude"]["type"]},
                    output_config={"effort": self.cfg["thinking"]["claude"]["effort"]},
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": "Subtitles:\n" + chunk},
                    ]}]) as stream:
                output = "".join(stream.text_stream)
                return stream.get_final_message(), output
        with _trace_stage(provider="claude", model=self.cfg["thinking"]["claude"]["model"],
                          thinking=self.cfg["thinking"]["claude"]["type"],
                          effort=self.cfg["thinking"]["claude"]["effort"]):
            return _model_request(consume, lambda result: _claude_response_text(*result))

    def transcribe(self, filename, lang):
        raise ValueError("Claude requires subtitles")


def _run_model_transcription(filename, engine_factory, summarize=True):
    existing = _TRACE_CONTEXT.get() or {}
    engine_name = existing.get("engine") or getattr(engine_factory, "__name__", type(engine_factory).__name__)
    with _trace_stage(filename=filename, engine=engine_name, stage="pipeline"):
        cfg = get_config()
        lang = detect_language(Path("yt-video", filename + ".desc")) or "en"
        title, description = get_video_title_and_description(filename) or ("", "")
        _trace("pipeline.start", language=lang, summarize=summarize)
        engine = None

        def request(prompt, chunk):
            nonlocal engine
            if engine is None:
                engine = engine_factory(cfg)
            return engine.request(prompt, chunk)

        with _trace_stage(stage="subtitles"):
            _trace("subtitles.lookup.start")
            text, needs_formatting = download_subtitles(filename)
            text = _speech(text)
            _trace("subtitles.lookup.completed", usable=_usable(text),
                   input_chars=len(text), needs_formatting=needs_formatting)
        if not _usable(text):
            with _trace_stage(stage="audio_transcription"):
                engine = engine_factory(cfg)
                acquired = engine.transcribe(filename, lang)
            if isinstance(acquired, _PageText):
                return _enrich_raw_subtitles(
                    filename, _require_text(acquired.text, "page text"), description)
            text = save_raw_subtitles(filename, acquired)
            _trace("raw.saved", output_chars=len(text))
            needs_formatting = True
        if needs_formatting:
            text = _enrich_raw_subtitles(filename, text, description)
        else:
            _check_raw_duplicate(filename, text, persist_raw=False)
        source_text = text
        if needs_formatting:
            with _trace_stage(stage="formatting"):
                _trace("formatting.start", input_chars=len(text))
                text = _map_text(text, lambda chunk: request(
                    make_prompt(lang, False, title, description), chunk))
                _trace("formatting.completed", output_chars=len(text))
        else:
            _trace("formatting.skipped", reason="readable_cached_text")
        text = _translate_if_needed(text, request)
        if needs_formatting or text != source_text:
            save_subtitles(filename, text)
            _trace("full_text.saved", output_chars=len(text))
        if not summarize or len(text) <= cfg["summarize_min_length"]:
            _trace("summary.skipped", reason="disabled" if not summarize else "short_text",
                   input_chars=len(text), minimum_chars=cfg["summarize_min_length"])
            return text
        with _trace_stage(stage="summary"):
            _trace("summary.start", input_chars=len(text))
            output = _map_text(text, lambda chunk: request(
                make_prompt(lang, True, title, description), chunk))
            _trace("summary.completed", output_chars=len(output))
            return output


def run_openai(filename, summarize=True):
    return _run_model_transcription(filename, _OpenAIEngine, summarize)


def run_gemini(filename, summarize=True):
    return _run_model_transcription(filename, _GeminiEngine, summarize)


def run_claude(filename, summarize=True):
    return _run_model_transcription(filename, _ClaudeEngine, summarize)


def _postprocess(filename):
    title, description = get_video_title_and_description(filename)
    duplicate = find_duplicate_episode(
        {"id": filename, "title": title or "", "description": description or ""},
        threshold=get_config()["duplicate_detection_threshold_transcription"])
    if duplicate is not None:
        mark_episode_as_duplicate(filename, duplicate)
    save_news_vector(filename)


def transcribe_video(filename, engine):
    path = Path("yt-video", filename + ".txt")
    reference = Path("yt-video", filename + ".desc")
    key = _retry_key(filename, engine)
    with _trace_stage(filename=filename, engine=engine, stage="job"):
        _trace("job.start")
        try:
            if not _cache_is_valid(path):
                engines = {"openai_s": run_openai, "gemini_s": run_gemini,
                           "claude_s": run_claude, "srt": run_srt}
                if engine not in engines:
                    raise ValueError(f"Unknown transcript engine: {engine}")
                text = _require_text(engines[engine](filename), f"{engine} final result")
                _atomic_text(path, text, reference)
                _trace("result.saved", output_chars=len(text))
                print(f"Transcription for {filename} completed")
                _POSTPROCESS_PENDING.add(key)
            else:
                _trace("result.cache_hit")
            if key in _POSTPROCESS_PENDING:
                _trace("postprocess.start")
                _postprocess(filename)
                _POSTPROCESS_PENDING.discard(key)
                _trace("postprocess.completed")
            _trace("job.completed")
            return True
        except TranscriptionDuplicate as duplicate:
            _POSTPROCESS_PENDING.discard(key)
            _trace("job.duplicate_skipped", duplicate_of=duplicate.duplicate_of)
            return True
        except Exception as error:
            _trace("job.failed", level="ERROR", error_type=type(error).__name__)
            print(f"Transcription failed for {filename} [{engine}]: {type(error).__name__}: {error}")
            try:
                write_log(filename, f"Transcription error [{engine}]: {type(error).__name__}: {error}")
            except OSError as log_error:
                print(f"Could not write transcription log: {log_error}")
            return False
        finally:
            _cleanup_temporary_audio(filename)


def run_transcription():
    secure_wait()
    cfg = get_config()
    engines = get_engine_map(cfg)
    names = list(engines)
    if cfg["auto_transcript_engine"] in engines:
        names.append(AUTO_YOUTUBE)
    if cfg["auto_transcript_engine_rss"] in engines:
        names.append(AUTO_RSS)
    batch = transcription_queues.drain(names)
    for engine in engines:
        queues = [(engine, batch[engine])]
        if cfg["auto_transcript_engine"] == engine:
            queues.append((AUTO_YOUTUBE, batch.get(AUTO_YOUTUBE, [])))
        if cfg["auto_transcript_engine_rss"] == engine:
            queues.append((AUTO_RSS, batch.get(AUTO_RSS, [])))
        for queue, filenames in queues:
            retry = []
            for filename in filenames:
                key = _retry_key(filename, engine)
                with _RETRY_LOCK:
                    attempts, due = _RETRY_STATE.get(key, (0, 0))
                if due > monotonic():
                    retry.append(filename)
                    continue
                if _cache_is_valid(Path("yt-video", filename + ".txt")) and key not in _POSTPROCESS_PENDING:
                    continue
                try:
                    success = transcribe_video(filename, engine=engine)
                except Exception as error:
                    # One failure, including logging/postprocessing, cannot drop the drained batch.
                    print(f"Transcription worker error for {filename}: {type(error).__name__}: {error}")
                    success = False
                if success is False:
                    with _RETRY_LOCK:
                        delay = min(3600, 60 * 2 ** min(attempts, 6))
                        _RETRY_STATE[key] = (attempts + 1, monotonic() + delay)
                    with _trace_stage(filename=filename, engine=engine, stage="queue"):
                        _trace("retry.scheduled", attempt=attempts + 1, delay_seconds=delay)
                    retry.append(filename)
                else:
                    with _RETRY_LOCK:
                        _RETRY_STATE.pop(key, None)
            if retry:
                transcription_queues.restore(queue, retry)


if __name__ == "__main__":
    run_transcription()
