from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

ACE_DIR = Path.home() / ".ace"
SESSIONS_DIR = ACE_DIR / "sessions"
CACHE_DIR = ACE_DIR / "cache"
BROWSER_PROFILE_DIR = ACE_DIR / "browser-profile"
DEBUG_PORT_FILE = ACE_DIR / "debug_port"
RUN_LOCK_FILE = ACE_DIR / "run.lock"
CONFIG_FILE = ACE_DIR / "config.toml"

# Look for .env in the project directory OR home dir
_ENV_FILE = Path(".env") if Path(".env").exists() else Path.home() / ".ace" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_prefix="ACE_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    default_model: str = "claude-haiku-4-5-20251001"
    fallback_model: str = "claude-sonnet-4-6"
    confidence_threshold: float = 0.65
    max_context_chunks: int = 5
    debug_port_start: int = 9222
    debug_port_end: int = 9232


def get_settings() -> Settings:
    return Settings()


def ensure_dirs() -> None:
    for d in (ACE_DIR, SESSIONS_DIR, CACHE_DIR, BROWSER_PROFILE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            from rich.console import Console
            Console().print(
                f"[bold red]Error:[/bold red] Cannot create directory: {d}\n"
                "Check that your home directory is writable."
            )
            raise SystemExit(1)
