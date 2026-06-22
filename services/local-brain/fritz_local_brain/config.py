"""Configuration for the Local Brain service."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_COMPILE_MAX_CAPTURES = 25


class Settings(BaseSettings):
    """Environment-driven settings.

    API keys are optional. Empty strings are treated as unset by callers.
    """

    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    brain_home: Path = Field(default=Path("/data/brain"), validation_alias=AliasChoices("LOCAL_BRAIN_HOME", "BRAIN_HOME"))
    brain_store_path: Path | None = Field(default=None, validation_alias=AliasChoices("LOCAL_BRAIN_STORE_PATH", "BRAIN_STORE_PATH"))
    skills_dir: Path = Field(default=Path("/app/skills"), validation_alias=AliasChoices("LOCAL_BRAIN_SKILLS_DIR", "SKILLS_DIR"))
    path_map: str = Field(default="", validation_alias=AliasChoices("LOCAL_BRAIN_PATH_MAP", "BRAIN_PATH_MAP"))

    scheduler_enabled: bool = Field(default=False, validation_alias=AliasChoices("LOCAL_BRAIN_SCHEDULER_ENABLED", "SCHEDULER_ENABLED"))
    scheduler_dry_run: bool = Field(default=True, validation_alias=AliasChoices("LOCAL_BRAIN_SCHEDULER_DRY_RUN", "SCHEDULER_DRY_RUN"))
    local_brain_autostart_installed: bool = Field(default=False, validation_alias=AliasChoices("LOCAL_BRAIN_AUTOSTART_INSTALLED", "AUTOSTART_INSTALLED"))
    interval_minutes: int = Field(default=30, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_INTERVAL_MINUTES", "BRAIN_INTERVAL_MINUTES"))

    llm_protocol: str = Field(default="openai-compatible", validation_alias=AliasChoices("LOCAL_BRAIN_LLM_PROTOCOL", "LLM_PROTOCOL"))
    llm_base_url: str = Field(default="http://host.docker.internal:11434/v1", validation_alias=AliasChoices("LOCAL_BRAIN_LLM_BASE_URL", "LLM_ENDPOINT", "LLM_BASE_URL"))
    llm_model: str = Field(default="local-instruct-model", validation_alias=AliasChoices("LOCAL_BRAIN_LLM_MODEL", "LLM_MODEL"))
    llm_api_key: str | None = Field(default=None, validation_alias=AliasChoices("LOCAL_BRAIN_LLM_API_KEY", "LLM_API_KEY"))
    llm_timeout_seconds: float = Field(default=120.0, validation_alias=AliasChoices("LOCAL_BRAIN_LLM_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"))

    embedding_enabled: bool = Field(default=False, validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_ENABLED", "EMBEDDING_ENABLED"))
    embedding_protocol: str = Field(default="openai-compatible", validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_PROTOCOL", "EMBEDDING_PROTOCOL"))
    embedding_base_url: str = Field(default="http://host.docker.internal:11434/v1", validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_BASE_URL", "EMBEDDING_ENDPOINT", "EMBEDDING_BASE_URL"))
    embedding_model: str = Field(default="nomic-embed-text:latest", validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_MODEL", "EMBEDDING_MODEL"))
    embedding_api_key: str | None = Field(default=None, validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_API_KEY", "EMBEDDING_API_KEY"))
    embedding_timeout_seconds: float = Field(default=60.0, validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_TIMEOUT_SECONDS", "EMBEDDING_TIMEOUT_SECONDS"))
    embedding_refresh_after_compile: bool = Field(default=True, validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_REFRESH_AFTER_COMPILE", "EMBEDDING_REFRESH_AFTER_COMPILE"))
    embedding_refresh_debounce_seconds: float = Field(default=300.0, ge=0.0, validation_alias=AliasChoices("LOCAL_BRAIN_EMBEDDING_REFRESH_DEBOUNCE_SECONDS", "EMBEDDING_REFRESH_DEBOUNCE_SECONDS"))

    api_host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("LOCAL_BRAIN_API_HOST", "API_HOST"))
    api_port: int = Field(default=8765, validation_alias=AliasChoices("LOCAL_BRAIN_API_PORT", "API_PORT"))
    api_token: str | None = Field(default=None, validation_alias=AliasChoices("LOCAL_BRAIN_API_TOKEN", "API_TOKEN"))
    approval_token: str | None = Field(default=None, validation_alias=AliasChoices("LOCAL_BRAIN_APPROVAL_TOKEN", "APPROVAL_TOKEN"))
    large_batch_threshold: int = Field(default=10, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_LARGE_BATCH_THRESHOLD", "LARGE_BATCH_THRESHOLD"))

    sync_skill_name: str = Field(default="brain-sync", validation_alias=AliasChoices("LOCAL_BRAIN_SYNC_SKILL_NAME", "SYNC_SKILL_NAME"))
    query_skill_name: str = Field(default="brain-query", validation_alias=AliasChoices("LOCAL_BRAIN_QUERY_SKILL_NAME", "QUERY_SKILL_NAME"))
    lint_skill_name: str = Field(default="brain-lint", validation_alias=AliasChoices("LOCAL_BRAIN_LINT_SKILL_NAME", "LINT_SKILL_NAME"))
    allow_first_external_sync: bool = Field(default=False, validation_alias=AliasChoices("LOCAL_BRAIN_ALLOW_FIRST_EXTERNAL_SYNC", "ALLOW_FIRST_EXTERNAL_SYNC"))
    capture_max_chars: int = Field(default=4000, ge=500, validation_alias=AliasChoices("LOCAL_BRAIN_CAPTURE_MAX_CHARS", "CAPTURE_MAX_CHARS"))
    compile_max_captures: int | None = Field(default=DEFAULT_COMPILE_MAX_CAPTURES, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_COMPILE_MAX_CAPTURES", "COMPILE_MAX_CAPTURES"))
    correlation_top_k: int = Field(default=5, ge=0, validation_alias=AliasChoices("LOCAL_BRAIN_CORRELATION_TOP_K", "CORRELATION_TOP_K"))
    correlation_max_chars: int = Field(default=4000, ge=0, validation_alias=AliasChoices("LOCAL_BRAIN_CORRELATION_MAX_CHARS", "CORRELATION_MAX_CHARS"))
    reconciliation_enabled: bool = Field(default=True, validation_alias=AliasChoices("LOCAL_BRAIN_RECONCILIATION_ENABLED", "RECONCILIATION_ENABLED"))
    reconciliation_autonomy: str = Field(default="apply", validation_alias=AliasChoices("LOCAL_BRAIN_RECONCILIATION_AUTONOMY", "RECONCILIATION_AUTONOMY"))
    bulk_supersession_threshold: int = Field(default=5, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_BULK_SUPERSESSION_THRESHOLD", "BULK_SUPERSESSION_THRESHOLD"))

    # WI12: mirror agent + retrieval-synthesis merge policy.
    # brain-first is the epic-confirmed default (epic §10): brain knowledge is
    # authoritative; external mirror matches only fill remaining gaps.
    merge_policy: str = Field(default="brain-first", validation_alias=AliasChoices("LOCAL_BRAIN_MERGE_POLICY", "MERGE_POLICY"))
    mirror_enabled: bool = Field(default=False, validation_alias=AliasChoices("LOCAL_BRAIN_MIRROR_ENABLED", "MIRROR_ENABLED"))
    mirror_interval_minutes: int = Field(default=60, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_MIRROR_INTERVAL_MINUTES", "MIRROR_INTERVAL_MINUTES"))

    # WI13: re-reconciliation sweep (disabled by default; dry-run by default so
    # nothing is written until an operator explicitly enables apply mode).
    rereconciliation_enabled: bool = Field(default=False, validation_alias=AliasChoices("LOCAL_BRAIN_RERECONCILIATION_ENABLED", "RERECONCILIATION_ENABLED"))
    rereconciliation_interval_minutes: int = Field(default=1440, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_RERECONCILIATION_INTERVAL_MINUTES", "RERECONCILIATION_INTERVAL_MINUTES"))
    rereconciliation_dry_run: bool = Field(default=True, validation_alias=AliasChoices("LOCAL_BRAIN_RERECONCILIATION_DRY_RUN", "RERECONCILIATION_DRY_RUN"))

    @field_validator("brain_store_path", mode="before")
    @classmethod
    def empty_brain_store_path_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("compile_max_captures", mode="before")
    @classmethod
    def empty_compile_max_captures_is_unset(cls, value: object) -> object:
        if value == "":
            return DEFAULT_COMPILE_MAX_CAPTURES
        if isinstance(value, str) and value.strip().lower() in {"all", "unbounded"}:
            return None
        return value

    @field_validator("reconciliation_autonomy", mode="before")
    @classmethod
    def validate_reconciliation_autonomy(cls, value: object) -> object:
        if not isinstance(value, str):
            return "apply"
        normalized = value.strip().lower()
        if not normalized:
            return "apply"
        if normalized not in {"apply", "propose"}:
            raise ValueError(f"reconciliation_autonomy must be 'apply' or 'propose', got: {value!r}")
        return normalized

    @field_validator("merge_policy", mode="before")
    @classmethod
    def validate_merge_policy(cls, value: object) -> object:
        if not isinstance(value, str):
            return "brain-first"
        normalized = value.strip().lower()
        if not normalized:
            return "brain-first"
        if normalized not in {"brain-first", "peer-ranked"}:
            raise ValueError(f"merge_policy must be 'brain-first' or 'peer-ranked', got: {value!r}")
        return normalized

    def resolve_brain_store_path(self) -> Path:
        """Resolve the brain-owned knowledge store root.

        The store is decoupled from the registry/vaults: when
        ``brain_store_path`` is set it wins (relocatable, ``~`` expanded);
        otherwise the store defaults to ``<brain_home>/knowledge``. No
        registry is consulted to locate the store.
        """

        if self.brain_store_path is not None:
            return Path(self.brain_store_path).expanduser()
        return Path(self.brain_home).expanduser() / "knowledge"

    def normalized_api_key(self) -> str | None:
        if self.llm_api_key and self.llm_api_key.strip():
            return self.llm_api_key
        return None

    def normalized_embedding_api_key(self) -> str | None:
        if self.embedding_api_key and self.embedding_api_key.strip():
            return self.embedding_api_key
        return None

    def approval_matches(self, token: str | None) -> bool:
        return bool(self.approval_token and token and token == self.approval_token)

    def normalized_llm_base_url(self) -> str:
        """Return a Docker-reachable LLM endpoint for host-local defaults."""

        return _normalized_container_url(self.llm_base_url)

    def normalized_embedding_base_url(self) -> str:
        """Return a Docker-reachable embedding endpoint for host-local defaults."""

        return _normalized_container_url(self.embedding_base_url)


def _normalized_container_url(url: str) -> str:
    parsed = urlsplit(url)
    if Path("/.dockerenv").exists() and parsed.hostname in {"localhost", "127.0.0.1"}:
        host = "host.docker.internal"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
