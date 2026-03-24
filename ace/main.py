import os
os.environ.setdefault("NODE_NO_WARNINGS", "1")

import typer
from rich.console import Console

from ace.config import ensure_dirs

app = typer.Typer(
    name="ace",
    help="Automated academic assignment tool — opens browser, reads your quiz, answers it, you approve.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


def _load_commands() -> None:
    from ace.cli.run import run_cmd
    from ace.cli.config import config_cmd
    from ace.cli.debug import debug_cmd

    app.command("run")(run_cmd)
    app.command("debug")(debug_cmd)
    app.add_typer(config_cmd, name="config")


_load_commands()


@app.callback()
def main() -> None:
    ensure_dirs()


if __name__ == "__main__":
    app()
