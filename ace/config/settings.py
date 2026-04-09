from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

ACE_DIR = Path.home() / ".ace"
SESSIONS_DIR = ACE_DIR / "sessions"
CACHE_DIR = ACE_DIR / "cache"
BROWSER_PROFILE_DIR = ACE_DIR / "browser-profile"
DEBUG_PORT_FILE = ACE_DIR / "debug_port"
RUN_LOCK_FILE = ACE_DIR / "run.lock"

_ENV_FILE = Path(".env") if Path(".env").exists() else ACE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_prefix="ACE_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Provider selection: groq | deepseek | anthropic
    provider: str = "groq"

    # API keys (unprefixed so standard env var names work)
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Per-provider model (can be overridden)
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    deepseek_model: str = "deepseek-chat"
    anthropic_model: str = "claude-sonnet-4-5"

    debug_port_start: int = 9222
    debug_port_end: int = 9232


def get_settings() -> Settings:
    return Settings()


def ensure_dirs() -> None:
    for d in (ACE_DIR, BROWSER_PROFILE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            from rich.console import Console
            Console().print(
                f"[bold red]Error:[/bold red] Cannot create directory: {d}\n"
                "Check that your home directory is writable."
            )
            raise SystemExit(1)
