#!/usr/bin/env python3

import argparse
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from threading import RLock

from lxml import etree

from config import get_config


REPOSITORY = Path(__file__).resolve().parent
DATA_DIRECTORY = REPOSITORY / "yt-video"
MODEL_NAME = "intfloat/multilingual-e5-small"
MAX_TEXT_CHARACTERS = 50_000
_CATEGORY_CACHE = OrderedDict()
_CATEGORY_CACHE_LOCK = RLock()


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden_depth += 1
        elif not self.hidden_depth and tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif not self.hidden_depth:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def plain_text(value):
    parser = _PlainTextParser()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


def local_name(element):
    return element.tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1] if isinstance(element.tag, str) else ""


def xml_field(entry, name):
    for element in entry:
        if local_name(element) == name:
            return "".join(element.itertext()).strip()
    return ""


def parse_entry(source):
    parser = etree.XMLParser(encoding="utf-8", recover=True,
                             resolve_entities=False, no_network=True)
    entry = etree.fromstring(source, parser)
    if entry is None or local_name(entry) != "entry":
        raise ValueError("Expected an XML entry.")
    return entry


def desc_path(fn, directory=None):
    if not isinstance(fn, str) or not fn or Path(fn).name != fn or fn in {".", ".."}:
        raise ValueError("fn must be a filename without a directory.")
    path = (DATA_DIRECTORY if directory is None else Path(directory)) / (fn + ".desc")
    if path.is_symlink():
        raise ValueError("Symbolic links are not supported for news metadata.")
    return path


@dataclass(frozen=True)
class News:
    fn: str
    path: Path
    source: bytes
    metadata: os.stat_result
    text: str

    @property
    def text_sha256(self):
        return hashlib.sha256(("query: " + self.text).encode("utf-8")).hexdigest()


def read_news(fn, directory=None):
    path = desc_path(fn, directory)
    with path.open("rb") as stream:
        source = stream.read()
        metadata = os.fstat(stream.fileno())
    entry = parse_entry(source)
    title = re.sub(r"^\[[^\]]+\]\s*", "", xml_field(entry, "title"))
    body = xml_field(entry, "summary")
    try:
        with path.with_suffix(".txt").open(encoding="utf-8") as stream:
            annotation = stream.read(MAX_TEXT_CHARACTERS)
        if annotation.strip():
            body = annotation
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError) as error:
        print(f"Could not read annotation for {fn}; using the description: {error}",
              file=sys.stderr)
    text = plain_text(title + "\n" + body[:MAX_TEXT_CHARACTERS])
    if not text:
        raise ValueError("No text is available for embedding.")
    return News(fn, path, source, metadata, text)


def valid_vector(vector, dimension):
    return (
        isinstance(vector, list) and len(vector) == dimension and dimension > 0
        and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) for value in vector)
        and abs(math.fsum(value * value for value in vector) - 1.0) < 0.001
    )


def stored_vector(source):
    entry = parse_entry(source)
    elements = [element for element in entry if local_name(element) == "embedding"]
    if len(elements) != 1:
        return None
    element = elements[0]
    try:
        dimension = int(element.get("dimensions", "0"))
        vector = [float(value) for value in (element.text or "").split()]
        signature = json.loads(element.get("signature", "null"))
        if (element.get("model") != MODEL_NAME or not isinstance(signature, dict)
                or not valid_vector(vector, dimension)):
            return None
        return vector, signature, element.get("text_sha256")
    except (TypeError, ValueError, OverflowError):
        return None


class Encoder:
    def __init__(self, model, signature, dimension):
        self.model = model
        self.signature = signature
        self.dimension = dimension
        self.vectors = {}
        self.hits = 0
        self.misses = 0

    def encode(self, texts, **options):
        if options.get("normalize_embeddings") is not True:
            raise ValueError("Only normalized embeddings are supported.")
        keys = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        missing = {}
        for key, text in zip(keys, texts, strict=True):
            if valid_vector(self.vectors.get(key), self.dimension):
                self.hits += 1
            else:
                missing[key] = text
        if missing:
            encoded = self.model.encode(list(missing.values()), **options)
            if len(encoded) != len(missing):
                raise ValueError("The model returned an incorrect number of embeddings.")
            for key, vector in zip(missing, encoded, strict=True):
                values = [float(value) for value in vector]
                if not valid_vector(values, self.dimension):
                    raise ValueError("The model returned an invalid normalized embedding.")
                self.vectors[key] = values
            self.misses += len(missing)
        return [self.vectors[key] for key in keys]


def load_model(threads=1):
    return _load_model(threads)


@lru_cache(maxsize=1)
def _load_model(threads = 1):
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    import torch
    from sentence_transformers import SentenceTransformer
    from importlib.metadata import version

    torch.set_num_threads(threads)
    model = SentenceTransformer(
        MODEL_NAME, device="cpu",
        trust_remote_code=False, model_kwargs={"use_safetensors": True},
    )
    model.max_seq_length = 512
    signature = {
        "model": MODEL_NAME,
        "revision": getattr(model[0].auto_model.config, "_commit_hash", None),
        "max_seq_length": model.max_seq_length,
        "sentence_transformers": version("sentence-transformers"),
        "torch": torch.__version__,
    }
    return Encoder(model, signature, model.get_embedding_dimension())

model = load_model()

def write_news_vector(item, vector, model):
    if not valid_vector(vector, model.dimension):
        raise ValueError("Cannot save an invalid normalized embedding.")
    current = read_news(item.fn, item.path.parent)
    if current.source != item.source or current.text != item.text:
        raise RuntimeError(f"News changed while its embedding was being computed: {item.fn}")
    entry = parse_entry(item.source)
    for element in list(entry):
        if local_name(element) == "embedding":
            entry.remove(element)
    namespace = etree.QName(entry).namespace
    tag = f"{{{namespace}}}embedding" if namespace else "embedding"
    element = etree.SubElement(entry, tag)
    element.set("model", MODEL_NAME)
    element.set("dimensions", str(model.dimension))
    element.set("text_sha256", item.text_sha256)
    element.set("signature", json.dumps(model.signature, separators=(",", ":"), sort_keys=True))
    element.text = " ".join(repr(float(value)) for value in vector)
    payload = etree.tostring(entry, encoding="utf-8") + b"\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=item.path.parent, prefix="." + item.path.name + ".",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            os.fchown(stream.fileno(), item.metadata.st_uid, item.metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(item.metadata.st_mode))
            stream.flush()
            os.fsync(stream.fileno())
            os.utime(stream.fileno(), ns=(item.metadata.st_atime_ns, item.metadata.st_mtime_ns))
        if item.path.read_bytes() != item.source:
            raise RuntimeError(f"Metadata changed before its embedding could be saved: {item.fn}")
        os.replace(temporary, item.path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_vectors(news, model, batch_size=8):
    result = [None] * len(news)
    pending = []
    for index, item in enumerate(news):
        saved = stored_vector(item.source)
        if (saved is not None and saved[1] == model.signature
                and saved[2] == item.text_sha256
                and valid_vector(saved[0], model.dimension)):
            result[index] = saved[0]
        else:
            pending.append((index, item))
    encoded = model.encode(
        ["query: " + item.text for _, item in pending], batch_size=batch_size,
        normalize_embeddings=True, show_progress_bar=False,
    ) if pending else []
    if len(encoded) != len(pending):
        raise ValueError("The model returned an incorrect number of embeddings.")
    for (index, item), vector in zip(pending, encoded, strict=True):
        values = [float(value) for value in vector]
        write_news_vector(item, values, model)
        result[index] = values
    return result, len(pending)


def save_news_vector(fn):
    item = read_news(fn)
    vectors, _ = save_vectors([item], model)
    return vectors[0]


def topic_vectors(model, topics, batch_size=8):
    if not isinstance(topics, dict):
        raise ValueError("topics must be an object containing category descriptions.")
    cache_key = json.dumps(topics, ensure_ascii=False, separators=(",", ":"))
    cached = getattr(model, "topic_cache", None)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    descriptions = []
    for name, prototypes in topics.items():
        if not isinstance(name, str) or not name or not isinstance(prototypes, (list, tuple)) or not prototypes:
            raise ValueError("Each topic must have a name and at least one prototype.")
        for prototype in prototypes:
            if (not isinstance(prototype, (list, tuple)) or not prototype
                    or any(not isinstance(text, str) or not text.strip() for text in prototype)):
                raise ValueError("Each topic prototype must contain nonempty descriptions.")
            descriptions.extend("query: " + text for text in prototype)
    encoded = model.encode(descriptions, batch_size=batch_size,
                           normalize_embeddings=True, show_progress_bar=False)
    if len(encoded) != len(descriptions):
        raise ValueError("The model returned an incorrect number of topic embeddings.")
    result = {}
    offset = 0
    for name, prototypes in topics.items():
        result[name] = []
        for prototype in prototypes:
            vectors = encoded[offset:offset + len(prototype)]
            if any(not valid_vector([float(value) for value in vector], model.dimension)
                   for vector in vectors):
                raise ValueError(f"Invalid topic embedding: {name}")
            average = [math.fsum(float(vector[i]) for vector in vectors) / len(vectors)
                       for i in range(model.dimension)]
            norm = math.sqrt(math.fsum(value * value for value in average))
            if not math.isfinite(norm) or norm == 0:
                raise ValueError(f"Invalid topic prototype: {name}")
            result[name].append([value / norm for value in average])
            offset += len(prototype)
    model.topic_cache = (cache_key, result)
    return result


@lru_cache(maxsize=8)
def _category_context(model, topics_key, signature_key, dimension, threshold, limit):
    return model, signature_key, dimension, topics_key, threshold, limit


def _cache_category(path, fingerprint, context, categories):
    if time.time_ns() - fingerprint[4] < 1_000_000_000:
        return
    with _CATEGORY_CACHE_LOCK:
        _CATEGORY_CACHE[path] = fingerprint, context, tuple(categories)
        _CATEGORY_CACHE.move_to_end(path)
        if len(_CATEGORY_CACHE) > 8192:
            _CATEGORY_CACHE.popitem(last=False)


def get_news_category(fn):
    try:
        path = desc_path(fn).absolute()
        metadata = path.stat()
        fingerprint = (
            metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns,
        )
        with _CATEGORY_CACHE_LOCK:
            cached = _CATEGORY_CACHE.get(path)
            if cached is not None and cached[0] == fingerprint:
                _CATEGORY_CACHE.move_to_end(path)
            else:
                cached = None
        if cached is not None and cached[1] is None:
            return []
        saved = stored_vector(path.read_bytes()) if cached is None else None
    except (OSError, etree.XMLSyntaxError, ValueError, UnicodeError):
        return []
    if cached is None and saved is None:
        _cache_category(path, fingerprint, None, [])
        return []
    topics = get_config("topics")
    if not topics:
        return []
    threshold = get_config("category_threshold")
    limit = get_config("max_news_categories")
    context = _category_context(
        model, json.dumps(topics, ensure_ascii=False, separators=(",", ":")),
        json.dumps(model.signature, sort_keys=True, separators=(",", ":")),
        model.dimension, threshold, limit,
    )
    if cached is not None and cached[1] is context:
        return list(cached[2])
    if saved is None:
        try:
            saved = stored_vector(path.read_bytes())
        except (OSError, etree.XMLSyntaxError, ValueError, UnicodeError):
            return []
    if saved is None:
        _cache_category(path, fingerprint, None, [])
        return []
    vector, signature, _ = saved
    categories = []
    if signature == model.signature and valid_vector(vector, model.dimension):
        centers = topic_vectors(model, topics)
        scores = {
            topic: max(math.fsum(a * b for a, b in zip(vector, center, strict=True))
                       for center in prototypes)
            for topic, prototypes in centers.items()
        }
        categories = [
            topic for topic in sorted(scores, key=scores.get, reverse=True)
            if scores[topic] > threshold
        ][:limit]
    _cache_category(path, fingerprint, context, categories)
    return categories


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("The value must be positive.")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compute news embeddings and store them in their .desc XML files.",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIRECTORY,
                        help="News directory; defaults to yt-video next to this script.")
    parser.add_argument("--batch-size", type=positive_integer, default=8,
                        help="Number of texts per batch; defaults to 8.")
    args = parser.parse_args(argv)
    if not args.data_dir.is_dir():
        parser.error(f"News directory does not exist: {args.data_dir}")
    paths = sorted(args.data_dir.glob("*.desc"))
    if not paths:
        print("No news metadata files found.", flush=True)
        return 0
    updated = 0
    reused = 0
    failed = 0
    for start in range(0, len(paths), args.batch_size):
        news = []
        for path in paths[start:start + args.batch_size]:
            try:
                news.append(read_news(path.stem, args.data_dir))
            except (OSError, ValueError, UnicodeError, etree.XMLSyntaxError) as error:
                failed += 1
                print(f"Could not read {path.name}: {error}", file=sys.stderr)
        if news:
            try:
                _, written = save_vectors(news, model, args.batch_size)
                updated += written
                reused += len(news) - written
            except (OSError, ValueError, RuntimeError, etree.XMLSyntaxError) as error:
                print(f"Could not save embeddings: {error}", file=sys.stderr)
                return 1
        done = min(start + args.batch_size, len(paths))
        if done == len(paths) or done // 128 != start // 128:
            print(f"Processed {done}/{len(paths)} metadata files.", flush=True)
    print(f"Completed: updated={updated}, already_present={reused}, failed={failed}.", flush=True)
    print(f"Text embeddings: memory_hits={model.hits}, computed={model.misses}.", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
