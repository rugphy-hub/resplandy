"""Load config.yaml and the environment. Secrets reach the code through environment variables only."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.yaml"
ENV_FILE = ROOT / ".env"
SECRET_NAMES = ("OPENROUTER_API_KEY", "OPENALEX_API_KEY", "CROSSREF_MAILTO", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


class ConfigError(Exception):
    """A configuration problem a person has to fix; the message says how."""


def family(model_id: str) -> str:
    """The model family of an OpenRouter id: the part before the slash."""
    return model_id.split("/", 1)[0].lower()


class Secrets:
    """Keys from the environment. They are never printed: repr shows only which are set."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._values = {name: (env.get(name) or "").strip() for name in SECRET_NAMES}

    def get(self, name: str) -> str:
        """The value of one key, or an empty string when it is not set."""
        return self._values.get(name, "")

    def require(self, name: str) -> str:
        """The value of one key; a ConfigError that says what to do when it is missing."""
        value = self.get(name)
        if not value:
            raise ConfigError(f"{name} is not set. Copy .env.example to .env, fill it in, and run again.")
        return value

    def __repr__(self) -> str:
        return "Secrets(" + ", ".join(f"{n}={'set' if v else 'missing'}" for n, v in self._values.items()) + ")"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Models(_Section):
    writer: str
    reviewer: str
    judge: str
    fallbacks: dict[str, list[str]] = {}
    structured_output: dict[str, Literal["json_schema", "tool"]] = {}

    @model_validator(mode="after")
    def _keep_the_judge_pinned_and_the_families_apart(self) -> "Models":
        if self.fallbacks.get("judge"):
            raise ValueError("the judge is pinned to one model and takes no fallback list")
        if set(self.fallbacks) - {"writer", "reviewer", "judge"}:
            raise ValueError("fallbacks are allowed for the writer and the reviewer only")
        writer_side = {family(m) for m in [self.writer, *self.fallbacks.get("writer", [])]}
        checker_side = {family(m) for m in [self.reviewer, self.judge, *self.fallbacks.get("reviewer", [])]}
        if writer_side & checker_side:
            raise ValueError(
                f"the judge and the reviewer must be a different model family from the writer and its fallbacks; "
                f"shared: {sorted(writer_side & checker_side)}"
            )
        return self


class OpenRouter(_Section):
    base_url: str
    require_parameters: bool
    timeout_seconds: float
    max_retries: int


class Limits(_Section):
    max_llm_calls_per_run: int
    max_cost_usd_per_run: float | None
    max_steps_per_agent: int
    max_papers_per_search: int
    max_claims_per_run: int
    max_queries: int
    max_library_hits_per_query: int
    max_library_pages_per_run: int
    max_sources_per_run: int
    max_gaps: int
    reviewer_rounds: int
    repair_rounds: int


class JudgeGate(_Section):
    seeded_tuning: int
    seeded_held_back: int
    max_wrong_passes: int
    min_label_agreement: float


class Verification(_Section):
    mode: Literal["calibrate", "deployed"]
    quote_min_score: float
    title_min_similarity: float
    year_warning_offset: int
    judge_gate: JudgeGate


class Library(_Section):
    default_folder: Path
    max_files: int
    doi_search_pages: int


class Tracker(_Section):
    spi_remind_below: float
    spi_replan_below: float
    k_min_tasks_per_type: int
    k_min: float
    k_max: float
    job_interval_minutes: int


class Notify(_Section):
    max_per_task_per_hour: int
    telegram: bool


class Apis(_Section):
    arxiv_min_interval_seconds: float
    http_cache_days: int
    timeout_seconds: float


class Guardrails(_Section):
    injection_patterns: list[str]


class Paths(_Section):
    database: Path
    cache: Path
    traces: Path


class Config(_Section):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    timezone: str
    models: Models
    openrouter: OpenRouter
    limits: Limits
    verification: Verification
    audit: dict = {}      # audit rounds and lessons are designed but not built; their numbers stay in config.yaml
    lessons: dict = {}
    library: Library
    tracker: Tracker
    notify: Notify
    apis: Apis
    guardrails: Guardrails
    paths: Paths
    secrets: Secrets = Field(default_factory=lambda: Secrets({}), exclude=True)
    replay: bool = False


def load_config(config_path: Path | str | None = None, env: Mapping[str, str] | None = None) -> Config:
    """Read config.yaml and the environment. Pass env to skip .env and os.environ, as the tests do."""
    path = Path(config_path or CONFIG_FILE)
    if env is None:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)  # research_tools reads the same names from the environment
        env = os.environ
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"{path} was not found. Run from the repository root.") from None
    try:
        cfg = Config(**raw)
    except ValidationError as err:
        raise ConfigError(f"{path.name} is not valid:\n{err}") from None
    cfg.secrets = Secrets(env)
    cfg.replay = (env.get("REPLAY") or "0").strip() == "1"
    for name in ("database", "cache", "traces"):
        value = getattr(cfg.paths, name)
        if not value.is_absolute():
            setattr(cfg.paths, name, ROOT / value)
    return cfg
