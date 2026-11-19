"""The cached, rate-limited door to the scholarly APIs. Every external HTTP call goes through here."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import httpx

from ..config import Config
from ..harness import digest

HEADERS = {"User-Agent": "resplandy/0.1 (research planning assistant)"}
_arxiv_lock = threading.Lock()
_arxiv_last_end = 0.0


class HttpReplayMiss(Exception):
    """REPLAY=1 and the answer is not in the HTTP cache."""


def cached(cfg: Config, namespace: str, key: dict, fetch: Callable[[], Any]) -> tuple[Any, bool]:
    """One call through the cache: (value, was_cached). The key must never hold a secret. REPLAY=1 never fetches."""
    path = cfg.paths.cache / "http" / namespace / f"{digest(key).removeprefix('sha256:')}.json"
    if path.exists():
        entry = json.loads(path.read_text(encoding="utf-8"))
        if cfg.replay or time.time() - entry["saved"] <= cfg.apis.http_cache_days * 86400:
            return entry["value"], True
    if cfg.replay:
        raise HttpReplayMiss(f"REPLAY=1 and no cached {namespace} answer.")
    value = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"saved": time.time(), "key": key, "value": value}, ensure_ascii=False),
                    encoding="utf-8")
    return value, False


@contextmanager
def arxiv_slot(cfg: Config) -> Iterator[None]:
    """Hold the one arXiv connection: one request at a time, apis.arxiv_min_interval_seconds apart."""
    global _arxiv_last_end
    with _arxiv_lock:
        wait = _arxiv_last_end + cfg.apis.arxiv_min_interval_seconds - time.monotonic()
        if _arxiv_last_end and wait > 0:
            time.sleep(wait)
        try:
            yield
        finally:
            _arxiv_last_end = time.monotonic()


def get(cfg: Config, url: str, params: dict | None = None, secret_params: dict | None = None,
        arxiv: bool = False) -> tuple[int, str]:
    """One cached GET: (status, body). Secret parameters are sent, but never stored and never part of the key."""

    def fetch() -> list:
        send = {**(params or {}), **{k: v for k, v in (secret_params or {}).items() if v}}

        def request() -> httpx.Response:
            return httpx.get(url, params=send, headers=HEADERS, timeout=cfg.apis.timeout_seconds,
                             follow_redirects=True)

        if arxiv:
            with arxiv_slot(cfg):
                response = request()
        else:
            response = request()
        if response.status_code not in (200, 404):
            response.raise_for_status()
        return [response.status_code, response.text]

    (status, body), _ = cached(cfg, "get", {"url": url, "params": params or {}}, fetch)
    return status, body
