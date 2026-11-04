from collections import Counter
from functools import partial
import re

from config import get_config

MAX_GROUPS = 64
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_BOUNDARY = re.compile(r"[.!?;:\n\r<>|•*«»“”\"/—–]+")
_URL = re.compile(r"https?://\S+")


def _stop_words():
    value = get_config("grouping_stop_words")
    if not isinstance(value, str):
        raise ValueError("The grouping_stop_words setting must be a space-separated string.")
    return frozenset(value.casefold().split())


def _phrases(text, stop_words):
    for segment in _BOUNDARY.split(_URL.sub(" ", text).casefold()):
        tokens = _WORD.findall(segment)
        for size in (1, 2, 3):
            for start in range(len(tokens) - size + 1):
                words = tokens[start:start + size]
                if all(len(word) >= 3 and word not in stop_words for word in words):
                    yield " ".join(words)


def _load_news(encoder):
    import numpy as np
    from lxml import etree
    import classify_news

    texts, vectors = [], []
    for path in sorted(classify_news.DATA_DIRECTORY.glob("*.desc")):
        if path.is_symlink() or path.with_suffix(".txt").is_symlink():
            continue
        try:
            item = classify_news.read_news(path.stem)
            entry = classify_news.parse_entry(item.source)
            if classify_news.xml_field(entry, "duplicate").lower() == "true":
                continue
            saved = classify_news.stored_vector(item.source)
            if (saved is None or saved[1] != encoder.signature
                    or saved[2] != item.text_sha256
                    or not classify_news.valid_vector(saved[0], encoder.dimension)):
                continue
            current = path.stat()
            if (current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
                    item.metadata.st_ino, item.metadata.st_size,
                    item.metadata.st_mtime_ns, item.metadata.st_ctime_ns):
                continue
            texts.append(item.text[:12000])
            vectors.append(saved[0])
        except (OSError, ValueError, UnicodeError, etree.XMLSyntaxError):
            continue
    return texts, np.asarray(vectors, dtype=np.float32)


def _group_names(texts, labels, centers, encoder, stop_words=None):
    import numpy as np
    from sklearn.feature_extraction.text import CountVectorizer

    if stop_words is None:
        stop_words = _stop_words()
    fallback = [f"Group {index + 1}" for index in range(len(centers))]
    if len(texts) < 3:
        return fallback
    vectorizer = CountVectorizer(analyzer=partial(_phrases, stop_words=stop_words), min_df=3, max_features=80000, dtype=np.int32)
    try:
        counts = vectorizer.fit_transform(texts)
    except ValueError:
        return fallback
    terms = vectorizer.get_feature_names_out()
    totals = np.vstack([np.asarray(counts[labels == index].sum(axis=0)).ravel()
                        for index in range(len(centers))]).astype(np.float64)
    support = np.vstack([np.asarray((counts[labels == index] > 0).sum(axis=0)).ravel()
                         for index in range(len(centers))])
    lengths = totals.sum(axis=1, keepdims=True)
    relative = np.divide(totals, lengths, out=np.zeros_like(totals), where=lengths > 0)
    idf = np.log1p(float(lengths.mean()) / np.maximum(totals.sum(axis=0), 1))
    scores = np.sqrt(relative) * idf[None, :]
    candidates = []
    for index in range(len(centers)):
        ranked = sorted(np.flatnonzero(support[index] >= 3),
                        key=lambda term: (-scores[index, term], terms[term]))
        ranked = [term for term in ranked if terms[term] != "unclassified"]
        singles = [term for term in ranked if " " not in terms[term]][:80]
        multiword = [term for term in ranked if " " in terms[term]][:160]
        candidates.append(singles + multiword)
    pool = sorted({term for group in candidates for term in group})
    if not pool:
        return fallback
    embeddings = np.asarray(encoder.encode(
        [str(terms[term]) for term in pool], batch_size=32,
        normalize_embeddings=True, show_progress_bar=False,
    ), dtype=np.float32)
    if (embeddings.shape != (len(pool), encoder.dimension)
            or not np.isfinite(embeddings).all()
            or not np.allclose(np.linalg.norm(embeddings, axis=1), 1, atol=0.001)):
        raise RuntimeError("The encoder returned invalid group-name embeddings.")
    lookup = {term: position for position, term in enumerate(pool)}
    names = []
    for index, center in enumerate(centers):
        norm = np.linalg.norm(center)
        if not candidates[index] or norm == 0:
            names.append(fallback[index])
            continue
        direction = center / norm
        best = min(candidates[index],
                   key=lambda term: (-float(embeddings[lookup[term]] @ direction), terms[term]))
        names.append(str(terms[best]))
    return names


def _unique_names(names):
    used, result = set(), []
    for index, name in enumerate(names):
        base = name if name.strip() and name != "unclassified" else f"Group {index + 1}"
        name, suffix = base, 2
        while name in used:
            name = f"{base} ({suffix})"
            suffix += 1
        used.add(name)
        result.append(name)
    return result


def find_groups(group_count):
    if isinstance(group_count, bool) or not isinstance(group_count, int):
        raise ValueError("The number of groups must be a whole number.")
    if not 0 <= group_count <= MAX_GROUPS:
        raise ValueError(f"The number of groups must be between 0 and {MAX_GROUPS}.")
    if group_count == 0:
        return {}

    import numpy as np
    from sklearn.cluster import KMeans
    from threadpoolctl import threadpool_limits
    from classify_news import model

    stop_words = _stop_words()
    texts, vectors = _load_news(model)
    if len(vectors) < group_count:
        raise ValueError(
            f"Requested {group_count} groups, but only {len(vectors)} current news embeddings are available."
        )
    distinct = len(np.unique(vectors, axis=0))
    if distinct < group_count:
        raise ValueError(
            f"Requested {group_count} groups, but only {distinct} distinct news embeddings are available."
        )
    with threadpool_limits(limits=2):
        estimator = KMeans(n_clusters=group_count, n_init=30, random_state=42, algorithm="lloyd")
        raw = estimator.fit_predict(vectors)
    sizes = Counter(int(value) for value in raw)
    if len(sizes) != group_count:
        raise RuntimeError("Clustering did not produce the requested number of groups.")
    order = sorted(sizes, key=lambda value: (-sizes[value], value))
    mapping = {value: index for index, value in enumerate(order)}
    labels = np.asarray([mapping[int(value)] for value in raw])
    centers = np.asarray([vectors[labels == index].mean(axis=0)
                          for index in range(group_count)], dtype=np.float32)
    names = _unique_names(_group_names(texts, labels, centers, model, stop_words))
    return {name: [float(value) for value in center]
            for name, center in zip(names, centers, strict=True)}
