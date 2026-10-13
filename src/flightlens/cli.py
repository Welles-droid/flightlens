"""Command-line entry point: ``flightlens analyze LOG``."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from flightlens import FlightLensError, __version__
from flightlens.loader import AXES, load
from flightlens.report import render_json, render_markdown, render_table
from flightlens.verdict import Verdict, build_verdict

EXIT_OK, EXIT_PROBLEMS, EXIT_ERROR = 0, 1, 2

app = typer.Typer(add_completion=False, help="PID-tuning verdicts from ArduPilot DataFlash logs.")


class Format(str, Enum):
    table = "table"
    json = "json"
    markdown = "markdown"


Axis = Enum("Axis", {a: a for a in AXES}, type=str)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"flightlens {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """PID-tuning verdicts from ArduPilot DataFlash logs."""


def _error(message: str) -> None:
    Console(stderr=True).print(f"[red]Error:[/] {message}", highlight=False)


def _emit(verdict: Verdict, format: Format, output: Path | None, log: Path) -> None:
    if format is Format.table:
        if output is None:
            render_table(verdict, Console())
        else:
            with output.open("w") as f:
                render_table(verdict, Console(file=f, width=100))
        return
    text = render_json(verdict) if format is Format.json else render_markdown(verdict, log)
    if output is None:
        typer.echo(text)
    else:
        output.write_text(text + "\n")


@app.command()
def analyze(
    log: Annotated[Path, typer.Argument(help="DataFlash .BIN log to analyse.")],
    format: Annotated[Format, typer.Option("--format", "-f", help="Output format.")] = Format.table,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Write the report to this file.")
    ] = None,
    axis: Annotated[Optional[Axis], typer.Option("--axis", help="Analyse only this axis.")] = None,
) -> None:
    """Analyse LOG and print a tune verdict.

    Exit codes: 0 well tuned, 1 problems found, 2 error.
    """
    try:
        verdict = build_verdict(load(log), axes=[axis.value] if axis else None)
    except FlightLensError as exc:
        _error(str(exc))
        raise typer.Exit(EXIT_ERROR) from exc

    try:
        _emit(verdict, format, output, log)
    except OSError as exc:
        _error(f"Cannot write report: {exc}")
        raise typer.Exit(EXIT_ERROR) from exc

    raise typer.Exit(EXIT_OK if verdict.overall_grade == "well_tuned" else EXIT_PROBLEMS)
