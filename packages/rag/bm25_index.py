from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from packages.platform import get_settings
from packages.rag.chunker import TextChunk

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.strip()]


@dataclass
class Bm25Document:
    chunk_id: str
    kb_id: str
    version: int
    source_uri: str
    offset: int
    text: str
    tokens: list[str]


class Bm25Index:
    """轻量 BM25 倒排索引（进程内文件持久化）。"""

    def __init__(self, documents: list[Bm25Document]) -> None:
        self.documents = documents
        self._doc_len = [len(d.tokens) for d in documents]
        self._avgdl = sum(self._doc_len) / len(documents) if documents else 0.0
        self._df: dict[str, int] = {}
        self._postings: dict[str, list[tuple[int, int]]] = {}
        for i, doc in enumerate(documents):
            seen: set[str] = set()
            for term in doc.tokens:
                if term in seen:
                    continue
                seen.add(term)
                self._df[term] = self._df.get(term, 0) + 1
            for term, freq in _term_freq(doc.tokens).items():
                self._postings.setdefault(term, []).append((i, freq))
        self._N = len(documents)

    def search(self, query: str, top_k: int) -> list[tuple[Bm25Document, float]]:
        if not self.documents:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = [0.0] * len(self.documents)
        k1, b = 1.5, 0.75
        for term in set(q_tokens):
            postings = self._postings.get(term)
            if not postings:
                continue
            df = self._df.get(term, 0)
            idf = math.log(1 + (self._N - df + 0.5) / (df + 0.5))
            for doc_idx, tf in postings:
                dl = self._doc_len[doc_idx]
                denom = tf + k1 * (1 - b + b * dl / (self._avgdl or 1.0))
                scores[doc_idx] += idf * (tf * (k1 + 1)) / (denom or 1.0)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        out: list[tuple[Bm25Document, float]] = []
        for idx, score in ranked[:top_k]:
            if score <= 0:
                break
            out.append((self.documents[idx], score))
        return out


def _term_freq(tokens: list[str]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    return freq


def index_path(kb_id: str, version: int) -> Path:
    root = get_settings().rag_data_root / "bm25"
    return root / kb_id / f"v{version}.json"


def build_index_from_chunks(
    chunks: list[TextChunk],
    *,
    kb_id: str,
    version: int,
) -> Bm25Index:
    return Bm25Index(documents_from_chunks(chunks, kb_id=kb_id, version=version))


def documents_from_chunks(
    chunks: list[TextChunk],
    *,
    kb_id: str,
    version: int,
) -> list[Bm25Document]:
    return [
        Bm25Document(
            chunk_id=c.chunk_id,
            kb_id=kb_id,
            version=version,
            source_uri=c.source_uri,
            offset=c.offset,
            text=c.text,
            tokens=tokenize(c.text),
        )
        for c in chunks
    ]


def merge_source_into_index(
    existing: Bm25Index | None,
    chunks: list[TextChunk],
    *,
    kb_id: str,
    version: int,
    source_uri: str,
) -> Bm25Index:
    """替换单个 source 的 BM25 文档，保留其他 source。"""
    kept: list[Bm25Document] = []
    if existing is not None:
        kept = [d for d in existing.documents if d.source_uri != source_uri]
    incoming = documents_from_chunks(chunks, kb_id=kb_id, version=version)
    return Bm25Index(kept + incoming)


def remove_source_from_index(existing: Bm25Index, *, source_uri: str) -> Bm25Index | None:
    kept = [d for d in existing.documents if d.source_uri != source_uri]
    if not kept:
        return None
    return Bm25Index(kept)


def save_index(index: Bm25Index, kb_id: str, version: int) -> Path:
    path = index_path(kb_id, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "chunk_id": d.chunk_id,
            "kb_id": d.kb_id,
            "version": d.version,
            "source_uri": d.source_uri,
            "offset": d.offset,
            "text": d.text,
            "tokens": d.tokens,
        }
        for d in index.documents
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_index(kb_id: str, version: int) -> Bm25Index | None:
    path = index_path(kb_id, version)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return None
    docs: list[Bm25Document] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        docs.append(
            Bm25Document(
                chunk_id=str(item["chunk_id"]),
                kb_id=str(item.get("kb_id", kb_id)),
                version=int(item.get("version", version)),
                source_uri=str(item.get("source_uri", "")),
                offset=int(item.get("offset", 0)),
                text=str(item.get("text", "")),
                tokens=[str(t) for t in item.get("tokens", [])],
            )
        )
    return Bm25Index(docs)
