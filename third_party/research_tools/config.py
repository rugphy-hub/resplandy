"""Settings for the workshop server.

Everything you configure lives in one file, `.env`, next to server.py.
`python setup_workshop.py` writes it for you, or you can edit it by hand.

The file is re-read on every tool call, so changing your library folder or your
API key takes effect immediately, without restarting your AI app.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
SAMPLE_LIBRARY = ROOT / "sample_library"
SKILLS_DIR = ROOT / "skills"
CACHE_DIR = ROOT / ".cache"

# The provider is detected from the key. OpenRouter and Google match Tutorial 1's defaults.
DEFAULT_MODELS = {
    "openrouter": "deepseek/deepseek-v4.1-flash",
    "google": "gemini-3.5-flash",
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-6-luna",
}
# OpenRouter and Google speak the OpenAI chat API at these addresses.
BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
}
PROVIDER_NAMES = {"openrouter": "OpenRouter", "google": "Google AI Studio",
                  "anthropic": "Anthropic", "openai": "OpenAI"}


@dataclass
class Settings:
    library_path: Path
    llm_api_key: str
    llm_model: str
    openalex_api_key: str
    llm_base_url: str = ""      # optional: any OpenAI-compatible server, for example a local model

    @property
    def provider(self) -> str:
        """Which service the key belongs to: 'openrouter', 'google', 'anthropic', 'openai' or ''."""
        key = self.llm_api_key
        if not key:
            return ""
        if key.startswith("sk-or-"):
            return "openrouter"
        if key.startswith("AIza"):
            return "google"
        if key.startswith("sk-ant-"):
            return "anthropic"
        return "openai"

    @property
    def model(self) -> str:
        return self.llm_model or DEFAULT_MODELS.get(self.provider, "")

    @property
    def base_url(self) -> str | None:
        return self.llm_base_url or BASE_URLS.get(self.provider)

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAMES.get(self.provider, "the AI provider")


def load_settings(env_file: Path = ENV_FILE) -> Settings:
    """Read .env. A value in .env wins over a variable set in the shell."""
    file_values = dotenv_values(env_file) if env_file.exists() else {}

    def get(*names: str) -> str:
        for name in names:
            value = file_values.get(name) or os.environ.get(name)
            if value and value.strip():
                return value.strip()
        return ""

    folder = get("LIBRARY_PATH")
    return Settings(
        library_path=Path(folder).expanduser() if folder else SAMPLE_LIBRARY,
        llm_api_key=get("LLM_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY",
                        "ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
        llm_model=get("LLM_MODEL"),
        openalex_api_key=get("OPENALEX_API_KEY"),
        llm_base_url=get("LLM_BASE_URL"),
    )
