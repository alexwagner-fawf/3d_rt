"""Command line interface: `voxsol <step> config/<site>.yaml`."""

from __future__ import annotations

from voxsol import ensure_local_env

ensure_local_env()  # before numba/pyproj get imported by the step modules

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import typer  # noqa: E402

from voxsol.config import load_config  # noqa: E402

app = typer.Typer(help="ULS-based below-canopy insolation (see docs/PLAN.md).", no_args_is_help=True,
                  add_completion=False)  # completion install would write to ~/.bashrc


@app.callback()
def main() -> None:
    """voxsol pipeline steps."""


@app.command()
def inspect(
    config: Path = typer.Argument(..., exists=True, help="Site YAML config"),
    sample: int = typer.Option(3_000_000, help="Points read per LAZ file for attribute statistics"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print the full JSON report"),
) -> None:
    """Report LAS attributes, pulse grouping, trajectory time base/datum and logger coverage."""
    from voxsol.inspect import run_inspect

    cfg = load_config(config)
    report = run_inspect(cfg, sample_n=sample)
    if verbose:
        typer.echo(json.dumps(report, indent=2))
    typer.echo(f"Report written to {cfg.workdir / 'inspect.json'}")
    for line in report["assessment"]:
        typer.echo(f"- {line}")


def _register_step(name: str, doc: str) -> None:
    def command(config: Path = typer.Argument(..., exists=True, help="Site YAML config")) -> None:
        from voxsol.pipeline import STEPS

        report = STEPS[name](load_config(config))
        typer.echo(json.dumps(report, indent=2, default=str))

    command.__doc__ = doc
    app.command(name)(command)


for _name, _doc in (
    ("prepare", "Noise filter, CSF ground, DTM, pulse reconstruction, logger table."),
    ("voxelize", "Beam-based extinction per canopy voxel (above h_cut) and column metrics."),
    ("understory", "Per-pixel understory metrics (< h_cut) and orthomosaic RGB indices."),
    ("horizon", "Far-terrain horizon from the external DEM (skipped if none configured)."),
    ("forcing", "Radiation forcing (CAMS/ERA5/clear-sky) and reference temperatures."),
    ("trace", "Transmittance at logger disks (exact sun) and map pixels (sun bins + sky)."),
    ("irradiance", "Logger time series, logger covariates and daily maps."),
):
    _register_step(_name, _doc)


@app.command()
def run(
    config: Path = typer.Argument(..., exists=True, help="Site YAML config"),
    start: str = typer.Option("prepare", help="First step"),
    stop: str = typer.Option("irradiance", help="Last step"),
) -> None:
    """Run the pipeline steps from START to STOP."""
    from voxsol.pipeline import STEPS

    names = list(STEPS)
    cfg = load_config(config)
    for name in names[names.index(start): names.index(stop) + 1]:
        typer.echo(f"== {name}")
        report = STEPS[name](cfg)
        typer.echo(json.dumps(report, default=str))


@app.command()
def db(
    config: Path = typer.Argument(None, exists=True, help="Site YAML config with a `database` section"),
    check: bool = typer.Option(False, "--check", help="Only test the connection and print a database overview"),
    service: str = typer.Option(None, help="libpq service (default: database.service or 'voxsol')"),
) -> None:
    """Export TMS-4 data, logger positions and the ERA5 reference from PostgreSQL to work/<site>/db/."""
    import psycopg

    try:
        _db(config, check, service)
    except (ValueError, psycopg.Error) as exc:  # configuration/data/connection problems: message, no traceback
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)


def _db(config: Path | None, check: bool, service: str | None) -> None:
    import pandas as pd

    from voxsol.io.db import connect, export_site, overview

    cfg = load_config(config) if config else None
    if not check:
        if cfg is None:
            raise typer.BadParameter("a site config is required for the export (or use --check)")
        if service and cfg.database:
            cfg.database.service = service
        report = export_site(cfg)
        typer.echo(json.dumps(report, indent=2, default=str))
        typer.echo(f"Snapshot written to {cfg.db_dir}")
        unmapped = report["tms"].get("heights_not_in_calibration_sensors")
        if unmapped:
            typer.echo(f"warning: TMS heights {unmapped} match no sensor in calibration.sensors and will be "
                       f"dropped by the calibration (current: {cfg.calibration.sensors})", err=True)
        return
    name = service or (cfg.database.service if cfg and cfg.database else "voxsol")
    with connect(name) as conn:
        for key, value in overview(conn).items():
            if isinstance(value, pd.DataFrame):
                typer.echo(f"\n{key}:\n" + (value.to_string(index=False) if len(value) else "  (none)"))
            elif isinstance(value, list):
                typer.echo(f"{key}:\n  " + "\n  ".join(value))
            else:
                typer.echo(f"{key}: {value}")


@app.command()
def proxy(check: bool = typer.Option(False, "--check", help="Test the endpoints used by voxsol")) -> None:
    """Show the effective proxy/CA settings (from secrets/proxy.env) and optionally test connectivity."""
    from voxsol.netconfig import check_urls, describe

    for key, value in describe().items():
        typer.echo(f"{key:20s} {value}")
    if check:
        typer.echo("")
        for name, result in check_urls().items():
            typer.echo(f"{name:20s} {result}")


@app.command()
def validate(
    config: Path = typer.Argument(..., exists=True, help="Site YAML config"),
    surface_height: float = typer.Option(0.3, help="Height above the DTM of the surface seen in the ortho [m]"),
) -> None:
    """Shadow check: modeled direct light at the ortho time vs. dark areas in the orthomosaic."""
    from voxsol.validate import shadow_check

    typer.echo(json.dumps(shadow_check(load_config(config), surface_height), indent=2))


@app.command()
def synth(
    out: Path = typer.Option(Path("data/synthetic"), help="Output folder (inside the repo)"),
    config: Path = typer.Option(Path("config/synthetic.yaml"), help="Config file to write"),
    size: float = typer.Option(100.0, help="Scene size [m]"),
    loggers: int = typer.Option(24, help="Number of TMS loggers"),
    seed: int = typer.Option(1),
) -> None:
    """Generate a synthetic disturbed-forest site (LAZ, sbet, ortho, TMS long table, config)."""
    from voxsol.synthetic import make_demo_site

    typer.echo(json.dumps(make_demo_site(out, config, size=size, n_loggers=loggers, seed=seed), indent=2))


if __name__ == "__main__":
    app()
