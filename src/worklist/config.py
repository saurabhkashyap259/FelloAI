"""Settings loaded from the environment (and `.env` for local runs)."""

from functools import lru_cache

from urllib.parse import urlparse, urlunparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url_owner: str = "postgresql+psycopg://postgres:postgres@localhost:55432/worklist"
    database_url_web: str = "postgresql+psycopg://worklist_web:worklist_web@localhost:55432/worklist"
    database_url_job: str = "postgresql+psycopg://worklist_job:worklist_job@localhost:55432/worklist"
    session_secret: str = "dev-secret"
    session_hours: int = 12
    demo_mode: bool = False
    demo_password: str = "demo-pass-123"
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_s: float = 20.0
    cookie_secure: bool = False
    # Hosted deploys provide only the owner URL; the role URLs are derived from it with these passwords.
    worklist_web_password: str = ""
    worklist_job_password: str = ""

    @model_validator(mode="after")
    def _normalise_urls(self) -> "Settings":
        self.database_url_owner = _psycopg(self.database_url_owner)
        if self.worklist_web_password:
            self.database_url_web = _with_user(self.database_url_owner, "worklist_web", self.worklist_web_password)
        if self.worklist_job_password:
            self.database_url_job = _with_user(self.database_url_owner, "worklist_job", self.worklist_job_password)
        self.database_url_web, self.database_url_job = _psycopg(self.database_url_web), _psycopg(self.database_url_job)
        return self


def _psycopg(url: str) -> str:
    """Hosting platforms hand out postgres:// URLs; SQLAlchemy needs the psycopg 3 driver name."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _with_user(url: str, user: str, password: str) -> str:
    parts = urlparse(url)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunparse(parts._replace(netloc=f"{user}:{password}@{host}"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
