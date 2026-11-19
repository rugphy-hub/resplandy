"""Shared fixtures. Tests never touch the network, the real .env, the real cache or the real traces."""
import socket
from pathlib import Path

import pytest
import yaml

from resplandy.config import load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("Tests must not touch the network.")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def cfg(tmp_path):
    """The repo's config.yaml with an empty environment and every path moved into tmp_path."""
    config = load_config(ROOT / "config.yaml", env={})
    config.paths.database = tmp_path / "db.sqlite"
    config.paths.cache = tmp_path / "cache"
    config.paths.traces = tmp_path / "traces"
    return config


@pytest.fixture
def raw():
    """The repo's config.yaml as a plain dict, for tests that break it on purpose."""
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def write_config(tmp_path):
    def write(raw: dict) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        return path

    return write
