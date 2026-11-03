"""Runtime settings, read from the repo-root .env (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    medibridge_env: Literal["local", "test"] = "local"
    medibridge_api_host: str = "127.0.0.1"
    medibridge_api_port: int = 8000
    medibridge_web_origin: str = "http://127.0.0.1:5173"
    medibridge_session_secret: SecretStr
    medibridge_session_idle_minutes: int = Field(30, ge=5, le=240)
    medibridge_session_absolute_minutes: int = Field(480, ge=30, le=1440)
    medibridge_secure_cookies: Literal["auto", "1", "0"] = "auto"

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "medibridge"
    postgres_owner_user: str = "medibridge_owner"
    postgres_owner_password: SecretStr
    postgres_app_user: str = "medibridge_app"
    postgres_app_password: SecretStr

    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: SecretStr

    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_password: SecretStr

    llm_provider: Literal["ollama"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model_clinical: str = "qwen3.5:9b"
    llm_model_light: str = "qwen3.5:4b"
    embedding_model: str = "bge-m3"
    # Optional cross-encoder re-ranking of fused BM25 + dense candidates; empty (the default) disables it. On the
    # cardiology retrieval benchmark the tested local re-rankers lowered hit@3 and added ~250 ms (docs/evaluation.md).
    reranker_model: str = ""
    asr_model: str = "small.en"
    asr_compute_type: str = "int8"
    allow_hosted_phi: bool = False
    hosted_phi_approval_ref: str = ""

    document_store_dir: Path = Path("var/documents")
    audio_store_dir: Path = Path("var/audio")

    langsmith_tracing: bool = False
    langsmith_endpoint: str = ""
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "medibridge-demo"

    # Agent-to-agent (A2A): specialist agents run as separate local services; the supervisor calls them.
    agent_transport: Literal["a2a", "inprocess"] = "a2a"
    medibridge_a2a_secret: SecretStr
    a2a_host: str = "127.0.0.1"
    a2a_base_port: int = Field(8101, ge=1024, le=65000)
    a2a_call_timeout_s: float = Field(1500, ge=30, le=7200)
    a2a_token_ttl_s: int = Field(120, ge=10, le=900)

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        for name in ("document_store_dir", "audio_store_dir"):
            path = getattr(self, name)
            if not path.is_absolute():
                setattr(self, name, REPO_ROOT / path)
        # Agent traffic is plain HTTP carrying clinical data: it must never leave this machine.
        if self.a2a_host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("A2A_HOST must be a loopback address")
        if self.agent_transport == "a2a" and len(self.medibridge_a2a_secret.get_secret_value()) < 32:
            raise ValueError("MEDIBRIDGE_A2A_SECRET must be at least 32 characters")
        return self

    def database_url(self, role: Literal["app", "owner"] = "app") -> str:
        user, password = (
            (self.postgres_app_user, self.postgres_app_password)
            if role == "app"
            else (self.postgres_owner_user, self.postgres_owner_password)
        )
        return (
            f"postgresql+psycopg://{user}:{password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def checkpoint_conn_string(self, role: Literal["app", "owner"] = "app") -> str:
        """psycopg connection string for the LangGraph checkpointer (schema ``lg``)."""
        from urllib.parse import quote

        user, password = ((self.postgres_app_user, self.postgres_app_password) if role == "app"
                          else (self.postgres_owner_user, self.postgres_owner_password))
        return (f"postgresql://{quote(user)}:{quote(password.get_secret_value())}@{self.postgres_host}:"
                f"{self.postgres_port}/{self.postgres_db}?options=-csearch_path%3Dlg")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
