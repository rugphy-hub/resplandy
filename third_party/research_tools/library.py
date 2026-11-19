"""A small search engine over a folder of documents.

Plain Python: no MCP, no AI, no API key. server.py wraps it as MCP tools in a few
lines, which is the main lesson of the workshop: MCP is a thin layer around code
you already have.

How search works: each page is split into short overlapping passages, ranked with
BM25, the classic keyword-ranking formula. A passage scores higher when it contains
more of the query's words, especially words that are rare in your library. There are
no embeddings, so search matches words rather than meanings.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from .config import CACHE_DIR

SUPPORTED = {".pdf", ".txt", ".md"}
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = set(
    "a an and are as at be by can do does for from has have how in into is it its "
    "my of on or our that the their this to was were what which with".split()
)


def tokenize(text: str) -> list[str]:
    """Lower-case words without stop-words, with a crude plural fold
    (dendrites -> dendrite, batteries -> battery)."""
    words = []
    for word in _WORD.findall(text.lower()):
        if word in _STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        words.append(word)
    return words


def strip_running_lines(pages: list[str]) -> list[str]:
    """Remove header and footer lines that repeat on every page."""
    if len(pages) < 2:
        return pages

    def norm(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip())

    repeated = set.intersection(*[{norm(l) for l in p.splitlines() if l.strip()} for p in pages])
    return ["\n".join(l for l in p.splitlines() if norm(l) not in repeated) for p in pages]


@dataclass
class Hit:
    paper: str    # file name relative to the library folder
    title: str
    page: int     # 1-based, for citations
    score: float
    text: str     # the matching passage


class PaperLibrary:
    """Indexes every PDF, .txt and .md file in a folder and its subfolders.

    Extracted text is cached on disk, so a large library is slow only the first time.
    The index refreshes by itself when files are added, removed or changed.
    """

    def __init__(self, folder: str | Path, passage_words: int = 80, overlap: int = 20):
        self.folder = Path(folder)
        self.passage_words = passage_words
        self.overlap = overlap
        self.papers: dict[str, dict] = {}
        self.unreadable: list[str] = []   # e.g. scanned PDFs with no text layer
        self._signature: tuple | None = None
        self._passages: list[tuple[str, int, str]] = []
        self._tf: list[Counter] = []
        self._len: list[int] = []
        self._avg_len = 1.0
        self._idf: dict[str, float] = {}

    # ---- indexing ---------------------------------------------------------
    def _files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(
            p for p in self.folder.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED
            and not any(part.startswith(".") for part in p.relative_to(self.folder).parts)
        )

    def _read_pages(self, path: Path) -> tuple[str | None, list[str]]:
        """Return (title, pages), using the on-disk cache when the file is unchanged."""
        stat = path.stat()
        key = hashlib.sha256(f"{path}|{stat.st_mtime_ns}|{stat.st_size}".encode()).hexdigest()
        cache_file = CACHE_DIR / f"{key}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached["title"], cached["pages"]

        if path.suffix.lower() == ".pdf":
            reader = PdfReader(path)
            pages = strip_running_lines([page.extract_text() or "" for page in reader.pages])
            title = reader.metadata.title if reader.metadata else None
        else:
            pages, title = [path.read_text(encoding="utf-8", errors="replace")], None

        CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps({"title": title, "pages": pages}), encoding="utf-8")
        return title, pages

    def refresh(self) -> None:
        files = self._files()
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        if signature == self._signature:
            return
        self._signature = signature
        self.papers, self.unreadable, self._passages = {}, [], []

        step = self.passage_words - self.overlap
        for path in files:
            name = path.relative_to(self.folder).as_posix()
            try:
                title, pages = self._read_pages(path)
            except Exception:              # one damaged file must not stop the others
                self.unreadable.append(name)
                continue
            if not any(page.strip() for page in pages):
                self.unreadable.append(name)
                continue
            self.papers[name] = {"title": title or path.stem.replace("_", " "), "pages": pages}
            for page_no, text in enumerate(pages, start=1):
                words = text.split()
                for start in range(0, len(words), step):
                    self._passages.append((name, page_no, " ".join(words[start:start + self.passage_words])))
                    if start + self.passage_words >= len(words):
                        break

        # BM25 statistics
        self._tf = [Counter(tokenize(chunk)) for _, _, chunk in self._passages]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = sum(self._len) / len(self._len) if self._len else 1.0
        total = len(self._passages)
        doc_freq = Counter(word for tf in self._tf for word in tf)
        self._idf = {w: math.log(1 + (total - df + 0.5) / (df + 0.5)) for w, df in doc_freq.items()}

    # ---- public API -------------------------------------------------------
    def list_papers(self) -> list[dict]:
        self.refresh()
        return [{"file": name, "title": p["title"], "pages": len(p["pages"])}
                for name, p in self.papers.items()]

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        """Return the best passages, at most one per page."""
        self.refresh()
        query_words = set(tokenize(query))
        k1, b = 1.5, 0.75
        scored = []
        for i, tf in enumerate(self._tf):
            score = 0.0
            for word in query_words & tf.keys():
                f = tf[word]
                score += self._idf[word] * f * (k1 + 1) / (f + k1 * (1 - b + b * self._len[i] / self._avg_len))
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)

        hits, seen_pages = [], set()
        for score, i in scored:
            paper, page, text = self._passages[i]
            if (paper, page) in seen_pages:
                continue
            seen_pages.add((paper, page))
            hits.append(Hit(paper, self.papers[paper]["title"], page, round(score, 2), text))
            if len(hits) >= top_k:
                break
        return hits

    def resolve(self, name: str) -> str:
        """Find a file from its exact name or a unique part of it ('paper5' is enough)."""
        self.refresh()
        if name in self.papers:
            return name
        wanted = name.strip().lower()
        matches = [p for p in self.papers if wanted and wanted in p.lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError(f"'{name}' matches several files: {', '.join(matches)}")
        raise ValueError(f"No file matching '{name}'. Available: {', '.join(self.papers) or 'none'}")

    def read_page(self, paper: str, page: int) -> str:
        name = self.resolve(paper)
        pages = self.papers[name]["pages"]
        if not 1 <= page <= len(pages):
            raise ValueError(f"'{name}' has pages 1-{len(pages)}, not {page}.")
        return pages[page - 1]

    def full_text(self, paper: str) -> tuple[str, str]:
        """Return (file name, whole document with page markers)."""
        name = self.resolve(paper)
        pages = self.papers[name]["pages"]
        return name, "\n\n".join(f"[page {n}]\n{text}" for n, text in enumerate(pages, start=1))
