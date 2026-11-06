"""Sentence embeddings (all-MiniLM-L6-v2, same as memory_service) for similarity features.

Optional: if sentence-transformers is not installed, similarity features switch off and
everything else keeps working (the suggester falls back to word overlap).
"""

import threading
from typing import Optional

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DUPLICATE = 0.8
BATCH = 256

_model = None
_lock = threading.Lock()
_cache: dict = {"key": None, "ids": [], "authors": [], "matrix": None}


def available() -> bool:
    try:
        import numpy  # noqa: F401
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def model():
    global _model
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME, device="cpu")
        return _model


def encode(texts: list[str]):
    import numpy as np

    vecs = model().encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype="float32")


def to_blob(vec) -> bytes:
    return vec.astype("float32").tobytes()


def from_blob(blob: bytes):
    import numpy as np

    return np.frombuffer(blob, dtype="float32")


def build(project, job) -> dict:
    """Embed every message that has no vector yet, then refresh near-duplicate flags."""
    import numpy as np

    missing = project.all(
        "SELECT m.id, m.content_text FROM messages m LEFT JOIN message_embeddings e ON e.message_id = m.id "
        "WHERE e.message_id IS NULL AND length(trim(m.content_text)) > 0"
    )
    total = len(missing)
    for i in range(0, total, BATCH):
        if job.cancelled:
            return {"embedded": i}
        chunk = missing[i : i + BATCH]
        vecs = encode([m["content_text"] for m in chunk])
        with project.tx() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO message_embeddings (message_id, vector) VALUES (?, ?)",
                [(m["id"], to_blob(v)) for m, v in zip(chunk, vecs)],
            )
        job.progress(min(i + BATCH, total), total, "embedding messages")
    invalidate()

    ids, authors, matrix = load(project)
    flags = []
    if matrix is not None and len(ids) > 1:
        step = 256
        for start in range(0, len(ids), step):
            sims = matrix[start : start + step] @ matrix.T
            for r in range(sims.shape[0]):
                sims[r, start + r] = -1.0
            best = sims.argmax(axis=1)
            for r, j in enumerate(best):
                score = float(sims[r, j])
                if score >= DUPLICATE:
                    flags.append((ids[start + r], ids[int(j)], round(score, 3)))
            job.progress(min(start + step, len(ids)), len(ids), "finding near-duplicates")
    with project.tx() as conn:
        conn.execute("DELETE FROM message_similar")
        conn.executemany("INSERT INTO message_similar (message_id, other_id, score) VALUES (?, ?, ?)", flags)
    return {"embedded": total, "near_duplicates": len(flags)}


def invalidate() -> None:
    with _lock:
        _cache.update(key=None, ids=[], authors=[], matrix=None)


def load(project):
    """All message vectors as one normalized matrix, cached until the next build."""
    import numpy as np

    count = project.scalar("SELECT COUNT(*) FROM message_embeddings")
    cache_key = (str(project.path), count)
    with _lock:
        if _cache["key"] == cache_key:
            return _cache["ids"], _cache["authors"], _cache["matrix"]
    rows = project.all(
        "SELECT e.message_id, e.vector, m.author_id FROM message_embeddings e JOIN messages m ON m.id = e.message_id "
        "WHERE m.excluded = 0"
    )
    if not rows:
        return [], [], None
    matrix = np.vstack([from_blob(r["vector"]) for r in rows])
    ids = [r["message_id"] for r in rows]
    authors = [r["author_id"] for r in rows]
    with _lock:
        _cache.update(key=cache_key, ids=ids, authors=authors, matrix=matrix)
    return ids, authors, matrix


def nearest(project, message_id: str, exclude_author: Optional[str], limit: int, threshold: float) -> list[tuple[str, float]]:
    ids, authors, matrix = load(project)
    if matrix is None or message_id not in ids:
        return []
    i = ids.index(message_id)
    sims = matrix @ matrix[i]
    order = sims.argsort()[::-1]
    out = []
    for j in order:
        if j == i or (exclude_author and authors[j] == exclude_author):
            continue
        if sims[j] < threshold:
            break
        out.append((ids[j], float(sims[j])))
        if len(out) >= limit:
            break
    return out
