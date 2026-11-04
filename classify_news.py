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
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
MODEL_REVISION = "4328cf26390c98c5e3c738b4460a05b95f4911f5"
MAX_SEQUENCE_TOKENS = 128
CHUNK_OVERLAP_TOKENS = 16
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
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


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


class ChunkedModel:
    def __init__(self, model):
        self.model = model

    def encode(self, texts, batch_size=8, normalize_embeddings=False,
               show_progress_bar=False):
        if normalize_embeddings is not True:
            raise ValueError("Only normalized embeddings are supported.")
        if not texts:
            return []
        if batch_size < 1:
            raise ValueError("Batch size must be positive.")

        import numpy as np
        import torch

        tokenizer = self.model.tokenizer
        special_tokens = tokenizer.num_special_tokens_to_add(pair=False)
        chunks, owners, weights = [], [], []
        for index, text in enumerate(texts):
            encoded = tokenizer(
                text, max_length=MAX_SEQUENCE_TOKENS, truncation=True,
                stride=CHUNK_OVERLAP_TOKENS, return_overflowing_tokens=True,
                return_attention_mask=True, padding=False,
            )
            for position in range(len(encoded["input_ids"])):
                features = {key: encoded[key][position] for key in tokenizer.model_input_names
                            if key in encoded}
                payload_tokens = len(features["input_ids"]) - special_tokens
                weight = payload_tokens - (CHUNK_OVERLAP_TOKENS if position else 0)
                if weight <= 0:
                    raise ValueError("Text chunks must contain new payload tokens.")
                chunks.append(features)
                owners.append(index)
                weights.append(weight)

        owners = np.asarray(owners, dtype=np.int32)
        weights = np.asarray(weights, dtype=np.int32)
        dimension = self.model.get_embedding_dimension()
        sums = np.zeros((len(texts), dimension), dtype=np.float64)
        totals = np.zeros(len(texts), dtype=np.float64)
        with torch.inference_mode():
            for start in range(0, len(chunks), batch_size):
                end = min(start + batch_size, len(chunks))
                features = tokenizer.pad(chunks[start:end], padding=True, return_tensors="pt")
                vectors = self.model(features)["sentence_embedding"]
                vectors = torch.nn.functional.normalize(vectors, p=2, dim=1).cpu().numpy()
                for offset, chunk in enumerate(range(start, end)):
                    owner = owners[chunk]
                    sums[owner] += vectors[offset] * weights[chunk]
                    totals[owner] += weights[chunk]
        averages = sums / totals[:, None]
        norms = np.linalg.norm(averages, axis=1, keepdims=True)
        if not np.isfinite(averages).all() or not np.all(norms > 0):
            raise ValueError("The model returned invalid chunk embeddings.")
        return (averages / norms).astype(np.float32)


class Encoder:
    def __init__(self, model, signature, dimension):
        self.model = model
        self.signature = signature
        self.dimension = dimension
        self.vectors = {}
        self.hits = 0
        self.misses = 0
        self.lock = RLock()

    def encode(self, texts, **options):
        with self.lock:
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


def load_model(threads=2):
    return _load_model(threads)


@lru_cache(maxsize=1)
def _load_model(threads=2):
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    import torch
    from sentence_transformers import SentenceTransformer
    from importlib.metadata import version

    torch.set_num_threads(threads)
    model = SentenceTransformer(
        MODEL_NAME, revision=MODEL_REVISION, device="cpu",
        trust_remote_code=False, model_kwargs={"use_safetensors": True},
    )
    model.eval()
    if model.max_seq_length != MAX_SEQUENCE_TOKENS or not model.tokenizer.is_fast:
        raise ValueError("The encoder requires the pinned 128-token fast tokenizer.")
    signature = {
        "model": MODEL_NAME,
        "revision": MODEL_REVISION,
        "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
        "chunk_pooling": "l2_chunks_new_token_weighted_mean_l2",
        "input_prefix": "",
        "max_seq_length": model.max_seq_length,
        "sentence_transformers": version("sentence-transformers"),
        "torch": torch.__version__,
    }
    return Encoder(ChunkedModel(model), signature, model.get_embedding_dimension())

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
        [item.text for _, item in pending], batch_size=batch_size,
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


def topic_centroids(topics, dimension):
    if not isinstance(topics, dict):
        raise ValueError("topics must map group names to centroid vectors.")
    result = []
    for name, vector in topics.items():
        if (not isinstance(name, str) or not name.strip() or name == "unclassified"
                or not isinstance(vector, list) or len(vector) != dimension
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       or not math.isfinite(value) for value in vector)):
            raise ValueError("Each topic must have a name and a finite centroid matching the news dimension.")
        result.append((name, tuple(float(value) for value in vector)))
    return tuple(result)


@dataclass(frozen=True)
class CategoryContext:
    encoder: Encoder
    signature: dict
    dimension: int
    centroids: tuple


@lru_cache(maxsize=8)
def _category_context(model, topics_key, signature_key, dimension):
    centers = topic_centroids(json.loads(topics_key), dimension)
    return CategoryContext(model, json.loads(signature_key), dimension, centers)


def get_category_context(topics=None):
    if topics is None:
        topics = get_config("topics")
    return _category_context(
        model, json.dumps(topics, ensure_ascii=False, separators=(",", ":")),
        json.dumps(model.signature, sort_keys=True, separators=(",", ":")),
        model.dimension,
    )


def nearest_category(vector, context):
    return min(
        context.centroids,
        key=lambda item: math.dist(vector, item[1]),
    )[0]


def _cache_category(path, fingerprint, context, categories):
    if time.time_ns() - fingerprint[4] < 1_000_000_000:
        return
    with _CATEGORY_CACHE_LOCK:
        _CATEGORY_CACHE[path] = fingerprint, context, tuple(categories)
        _CATEGORY_CACHE.move_to_end(path)
        if len(_CATEGORY_CACHE) > 8192:
            _CATEGORY_CACHE.popitem(last=False)


def get_news_category(fn, context=None):
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
    expected = model if context is None else context
    if (cached is not None
            and (cached[1].encoder is not (model if context is None else context.encoder)
                 or cached[1].signature != expected.signature
                 or cached[1].dimension != expected.dimension)):
        try:
            saved = stored_vector(path.read_bytes())
        except (OSError, etree.XMLSyntaxError, ValueError, UnicodeError):
            return []
    if saved is not None and (saved[1] != expected.signature
                              or not valid_vector(saved[0], expected.dimension)):
        return []
    if context is None:
        context = get_category_context()
    if not context.centroids:
        return []
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
    if signature == context.signature and valid_vector(vector, context.dimension):
        categories = [nearest_category(vector, context)]
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
