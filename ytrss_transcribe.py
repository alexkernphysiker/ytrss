import os
import subprocess
import shlex
import sys
from pathlib import Path
from dataclasses import dataclass
from lxml import etree
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
_RETRY_STATE = {}
_POSTPROCESS_PENDING = set()
_TEMPORARY_AUDIO = {}
_RETRY_LOCK = RLock()


class TranscriptionPending(ValueError):
    """Source is not ready yet; keep the job for a later pass."""


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
    return "\n\n".join(_require_text(request(chunk), "processed chunk")
                       for chunk in _chunks(text))


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
                return content, path != srt_path
            path.unlink()
    link = get_video_link(description_path)
    if not link or not get_config().get("yt-dlp-enabled"):
        return "", False
    lang = detect_language(description_path) or "en"
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
            write_log(filename, f"Subtitle download failed (exit {result.returncode}).")
            return "", False
        for path in sorted(Path(directory).iterdir()):
            if path.suffix.lower() not in (".vtt", ".srt", ".ttml") or not path.is_file():
                continue
            content = _speech(path.read_text(encoding="utf-8"))
            if _usable(content):
                return save_raw_subtitles(filename, content), True
    return "", False


def save_raw_subtitles(filename, text):
    text = _require_text(text, "raw transcription")
    text = _require_text("\n".join(filter_subs(text, max_length=0)), "raw subtitles")
    _atomic_text(Path("yt-video", filename + ".raw_srt"), text,
                 Path("yt-video", filename + ".desc"))
    return text


def save_subtitles(filename, text):
    text = _require_text(_speech(text) if isinstance(text, str) else text, "subtitles")
    _atomic_text(Path("yt-video", filename + ".srt"), text,
                 Path("yt-video", filename + ".desc"))


def get_video_title_and_description(filename):
    description_path = "yt-video/" + filename + ".desc"
    if os.path.exists(description_path):
        parser1 = etree.XMLParser(encoding="utf-8", recover=True)
        entry = etree.parse(description_path, parser1)
        title = entry.find("title").text
        description_element = entry.find("summary")
        descr = description_element.text if description_element.text is not None else ""
        return title,descr

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
        save_subtitles(filename, text)
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
    if response.status != "completed":
        raise ValueError(f"OpenAI response was not completed: {response.status}")
    return _require_text(response.output_text, "OpenAI response")


def _gemini_response_text(response):
    candidates = response.candidates
    if not candidates:
        raise ValueError(f"Gemini returned no candidates; feedback={getattr(response, 'prompt_feedback', None)}")
    for candidate in candidates:
        reason = candidate.finish_reason
        reason = getattr(reason, "name", reason)
        if reason != "STOP":
            raise ValueError(f"Gemini generation did not finish normally: {reason}")
    return _require_text(response.text, "Gemini response")


def _translate_if_needed(text, request):
    text = _require_text(text, "translation source")
    if detect_language_from_text(text) not in get_config()["transcription-prompts"]:
        prompt = f"Translate the supplied text into {get_config()['default_language']}. Return only the translation."
        return _map_text(text, lambda chunk: request(prompt, chunk))
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


class _OpenAIEngine:
    def __init__(self, cfg):
        from openai import OpenAI
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg["thinking"]["openai"]["api_key"])

    def request(self, prompt, chunk):
        _require_text(chunk, "OpenAI input")
        return _openai_response_text(self.client.responses.create(
            model=self.cfg["thinking"]["openai"]["model"],
            input=prompt + "\nText:\n" + chunk,
            reasoning={"effort": self.cfg["thinking"]["openai"]["effort"]},
        ))

    def transcribe(self, filename, lang):
        _wait_for_youtube(filename)
        video = Path("yt-video", filename)
        audio = convert_video_to_audio(video) if video.exists() else get_enclosure_link(filename)
        if not audio:
            raise ValueError("No usable audio source.")
        parts = []
        chunks = split_mp3_file(audio)
        try:
            for chunk in chunks:
                with open(chunk, "rb") as stream:
                    response = self.client.audio.transcriptions.create(
                        model=self.cfg["thinking"]["openai"]["audio_model"], file=stream,
                        response_format="diarized_json", chunking_strategy="auto", language=lang,
                    )
                speech = []
                previous = None
                for segment in response.segments:
                    words = _require_text(segment.text, "audio segment")
                    speech.append(("\n- " if segment.speaker != previous else " ") + words)
                    previous = segment.speaker
                parts.append(_require_text("".join(speech), "audio transcript"))
        finally:
            if hasattr(chunks, "close"):
                chunks.close()
        return _require_text("\n".join(parts), "audio transcript")


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
        return _gemini_response_text(self.client.models.generate_content(
            model=self.cfg["thinking"]["gemini"]["model"], contents=[prompt, "Text:\n" + chunk],
            config=self.generation_config))

    def transcribe(self, filename, lang):
        youtube = get_video_link(Path("yt-video", filename + ".desc"))
        if youtube:
            _wait_for_youtube(filename)
            return _gemini_response_text(self.client.models.generate_content(
                model=self.cfg["thinking"]["gemini"]["model"], contents=self.types.Content(parts=[
                    self.types.Part.from_uri(file_uri=youtube, mime_type="video/mp4"),
                    self.types.Part.from_text(text=make_verbatim_prompt()),
                ]), config=self.generation_config))
        enclosure = get_enclosure_link(filename)
        if not enclosure:
            return self.page_text(filename, lang)
        audio = download_audio_file(enclosure, filename)
        if not audio:
            raise ValueError("Podcast audio download failed.")
        uploaded = self.client.files.upload(file=audio)
        try:
            return _gemini_response_text(self.client.models.generate_content(
                model=self.cfg["thinking"]["gemini"]["model"],
                contents=[uploaded, make_verbatim_prompt()],
                config=self.generation_config))
        finally:
            try:
                self.client.files.delete(name=uploaded.name)
            except Exception as error:
                write_log(filename, f"Audio upload cleanup failed: {type(error).__name__}")

    def page_text(self, filename, lang):
        interaction = self.client.interactions.create(
            model=self.cfg["thinking"]["gemini"]["model"],
            input=make_page_text_prompt(lang, get_page_link(filename)),
            tools=[{"type": "url_context"}, {"type": "google_search"}],
            generation_config={"thinking_level": self.cfg["thinking"]["gemini"]["thinking_level"]})
        if interaction.status != "completed":
            raise ValueError(f"Gemini page interaction not completed: {interaction.status}")
        blocks = []
        steps = getattr(interaction, "steps", None) or getattr(interaction, "outputs", [])
        for step in steps:
            if step.type == "text":
                blocks.append(step.text)
            elif step.type == "model_output":
                blocks.extend(block.text for block in step.content if block.type == "text")
        return _PageText(_require_text("\n".join(blocks), "Gemini page text"))


class _ClaudeEngine:
    def __init__(self, cfg):
        import anthropic
        self.cfg = cfg
        self.client = anthropic.Client(api_key=cfg["thinking"]["claude"]["api_key"])

    def request(self, prompt, chunk):
        _require_text(chunk, "Claude input")
        with self.client.messages.stream(
                model=self.cfg["thinking"]["claude"]["model"], max_tokens=16000,
                thinking={"type": self.cfg["thinking"]["claude"]["type"]},
                output_config={"effort": self.cfg["thinking"]["claude"]["effort"]},
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "text", "text": "Subtitles:\n" + chunk},
                ]}]) as stream:
            output = "".join(stream.text_stream)
            final = stream.get_final_message()
        if final.stop_reason != "end_turn":
            raise ValueError(f"Claude generation did not finish normally: {final.stop_reason}")
        return _require_text(output, "Claude response")

    def transcribe(self, filename, lang):
        raise ValueError("Claude requires subtitles")


def _run_model_transcription(filename, engine_factory, summarize=True):
    cfg = get_config()
    lang = detect_language(Path("yt-video", filename + ".desc")) or "en"
    title, description = get_video_title_and_description(filename) or ("", "")
    engine = engine_factory(cfg)
    text, needs_formatting = download_subtitles(filename)
    text = _speech(text)
    if not _usable(text):
        acquired = engine.transcribe(filename, lang)
        if isinstance(acquired, _PageText):
            return _require_text(acquired.text, "page text")
        text = save_raw_subtitles(filename, acquired)
        needs_formatting = True
    source_text = text
    if needs_formatting:
        text = _map_text(text, lambda chunk: engine.request(
            make_prompt(lang, False, title, description), chunk))
    text = _translate_if_needed(text, engine.request)
    if needs_formatting or text != source_text:
        save_subtitles(filename, text)
    if not summarize or len(text) <= cfg["summarize_min_length"]:
        return text
    return _map_text(text, lambda chunk: engine.request(
        make_prompt(lang, True, title, description), chunk))


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
    try:
        if not _cache_is_valid(path):
            engines = {"openai_s": run_openai, "gemini_s": run_gemini,
                       "claude_s": run_claude, "srt": run_srt}
            if engine not in engines:
                raise ValueError(f"Unknown transcript engine: {engine}")
            text = _require_text(engines[engine](filename), f"{engine} final result")
            _atomic_text(path, text, reference)
            print(f"Transcription for {filename} completed")
            _POSTPROCESS_PENDING.add(key)
        if key in _POSTPROCESS_PENDING:
            _postprocess(filename)
            _POSTPROCESS_PENDING.discard(key)
        return True
    except Exception as error:
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
                        _RETRY_STATE[key] = (attempts + 1, monotonic() + min(3600, 60 * 2 ** min(attempts, 6)))
                    retry.append(filename)
                else:
                    with _RETRY_LOCK:
                        _RETRY_STATE.pop(key, None)
            if retry:
                transcription_queues.restore(queue, retry)


if __name__ == "__main__":
    run_transcription()
