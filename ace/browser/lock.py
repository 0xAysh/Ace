"""
Simple file-based lock to prevent two `ace run` instances from
controlling the browser at the same time.
"""
import os
from contextlib import contextmanager
from typing import Generator

from rich.console import Console
from rich.prompt import Confirm

from ace.config import RUN_LOCK_FILE

console = Console()


def _lock_pid() -> int | None:
    try:
        return int(RUN_LOCK_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@contextmanager
def run_lock() -> Generator[None, None, None]:
    existing_pid = _lock_pid()

    if existing_pid is not None and _pid_alive(existing_pid):
        console.print(
            f"[bold yellow]Warning:[/bold yellow] Another `ace run` is already active (PID {existing_pid}).\n"
            "Running two instances at once will cause double-fills and conflicts."
        )
        if not Confirm.ask("  Proceed anyway?", default=False):
            raise SystemExit(0)

    try:
        RUN_LOCK_FILE.write_text(str(os.getpid()))
    except OSError:
        pass  # non-fatal — best-effort lock

    try:
        yield
    finally:
        try:
            if RUN_LOCK_FILE.exists() and _lock_pid() == os.getpid():
                RUN_LOCK_FILE.unlink()
        except OSError:
            pass
