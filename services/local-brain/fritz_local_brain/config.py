"""Configuration for the Local Brain service."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings.

    API keys are optional. Empty strings are treated as unset by callers.
    """

    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    brain_home: Path = Field(default=Path("/data/brain"), validation_alias=AliasChoices("LOCAL_BRAIN_HOME", "BRAIN_HOME"))
    skills_dir: Path = Field(default=Path("/app/skills"), validation_alias=AliasChoices("LOCAL_BRAIN_SKILLS_DIR", "SKILLS_DIR"))
    path_map: str = Field(default="", validation_alias=AliasChoices("LOCAL_BRAIN_PATH_MAP", "BRAIN_PATH_MAP"))

    scheduler_enabled: bool = Field(default=False, validation_alias=AliasChoices("LOCAL_BRAIN_SCHEDULER_ENABLED", "SCHEDULER_ENABLED"))
    scheduler_dry_run: bool = Field(default=True, validation_alias=AliasChoices("LOCAL_BRAIN_SCHEDULER_DRY_RUN", "SCHEDULER_DRY_RUN"))
    interval_minutes: int = Field(default=30, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_INTERVAL_MINUTES", "BRAIN_INTERVAL_MINUTES"))

    llm_protocol: str = Field(default="openai-compatible", validation_alias=AliasChoices("LOCAL_BRAIN_LLM_PROTOCOL", "LLM_PROTOCOL"))
    llm_base_url: str = Field(default="http://host.docker.internal:1234/v1", validation_alias=AliasChoices("LOCAL_BRAIN_LLM_BASE_URL", "LLM_ENDPOINT", "LLM_BASE_URL"))
    llm_model: str = Field(default="local-model", validation_alias=AliasChoices("LOCAL_BRAIN_LLM_MODEL", "LLM_MODEL"))
    llm_api_key: str | None = Field(default=None, validation_alias=AliasChoices("LOCAL_BRAIN_LLM_API_KEY", "LLM_API_KEY"))
    llm_timeout_seconds: float = Field(default=120.0, validation_alias=AliasChoices("LOCAL_BRAIN_LLM_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"))

    api_host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("LOCAL_BRAIN_API_HOST", "API_HOST"))
    api_port: int = Field(default=8765, validation_alias=AliasChoices("LOCAL_BRAIN_API_PORT", "API_PORT"))
    api_token: str | None = Field(default=None, validation_alias=AliasChoices("LOCAL_BRAIN_API_TOKEN", "API_TOKEN"))

    compile_skill_name: str = Field(default="fritz:brain-compile", validation_alias=AliasChoices("LOCAL_BRAIN_COMPILE_SKILL_NAME", "COMPILE_SKILL_NAME"))
    capture_max_chars: int = Field(default=4000, ge=500, validation_alias=AliasChoices("LOCAL_BRAIN_CAPTURE_MAX_CHARS", "CAPTURE_MAX_CHARS"))
    compile_max_captures: int = Field(default=1, ge=1, validation_alias=AliasChoices("LOCAL_BRAIN_COMPILE_MAX_CAPTURES", "COMPILE_MAX_CAPTURES"))

    def normalized_api_key(self) -> str | None:
        if self.llm_api_key and self.llm_api_key.strip():
            return self.llm_api_key
        return None

    def normalized_llm_base_url(self) -> str:
        """Return a Docker-reachable LLM endpoint for host-local defaults."""

        parsed = urlsplit(self.llm_base_url)
        if Path("/.dockerenv").exists() and parsed.hostname in {"localhost", "127.0.0.1"}:
            host = "host.docker.internal"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
        return self.llm_base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
