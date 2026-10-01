"""The `agentops` command line. Increment 1 task 1 exposes `--version`; `run` arrives in task 7."""

from typing import Annotated

import typer

from agentops import __version__

app = typer.Typer(help="Investigate a simulated production incident with cited evidence.")


def print_version_and_exit(is_requested: bool) -> None:
    if is_requested:
        typer.echo(f"agentops {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=print_version_and_exit,
            is_eager=True,
            help="Print the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Investigate a simulated production incident with cited evidence."""
