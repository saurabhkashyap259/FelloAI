"""`worklist` command line. The same commands run locally, in tests and as scheduled jobs.

Exit codes: 0 success, 1 failure, 2 partial (some tenants failed), 3 skipped (lock held or already done).
"""

from datetime import date

import typer

from worklist.log import log

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)
db_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")

EXIT_OK, EXIT_FAILED, EXIT_PARTIAL, EXIT_SKIPPED = 0, 1, 2, 3


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply migrations (roles, RLS policies, tables)."""
    from worklist.db.migrate import upgrade

    upgrade()
    log("db.upgraded")


@app.command("seed-demo")
def seed_demo(tenants: int = 4) -> None:
    """Create tenants, demo users and tenant settings. Idempotent. Requires DEMO_MODE."""
    from worklist.seed import seed_demo as run

    raise typer.Exit(run(tenants=tenants))


@app.command()
def generate(
    seed: int = 42,
    accounts: int = 5000,
    events: int = 200_000,
    history_days: int = 1095,
    feed_days: int = 30,
    out: str = "reports/data_profile.json",
    reset: bool = typer.Option(False, "--reset", help="Delete previously generated and derived data first"),
) -> None:
    """Generate the synthetic world and load it through the ingest path."""
    from worklist.synth.writer import generate_and_load

    raise typer.Exit(generate_and_load(seed, accounts, events, history_days, feed_days, out, reset))


@app.command()
def train(
    as_of: str = typer.Option(None, help="Cutoff date YYYY-MM-DD (default: sim clock)"),
    promote: bool = typer.Option(True, "--promote/--no-promote"),
    report: str = typer.Option(None, help="Report path (default reports/eval_<date>.json)"),
) -> None:
    """Train baseline + LightGBM, calibrate per tenant, evaluate, apply the promotion gate."""
    from worklist.model.pipeline import train_and_register

    raise typer.Exit(train_and_register(_date(as_of), promote, report, run_type="manual"))


@app.command()
def retrain() -> None:
    """Scheduled weekly retrain: train --promote with the gate, records the decision."""
    from worklist.model.pipeline import train_and_register

    raise typer.Exit(train_and_register(None, True, None, run_type="retrain"))


@app.command()
def evaluate(model_version: str = typer.Option(..., "--model-version")) -> None:
    """Re-run the evaluation for an existing model version."""
    from worklist.model.pipeline import reevaluate

    raise typer.Exit(reevaluate(model_version))


@app.command()
def backfill(
    from_: str = typer.Option(..., "--from", help="YYYY-MM-DD or -Nd relative to the sim clock"),
    to: str = typer.Option("today", "--to"),
    every: str = typer.Option("7d", "--every"),
) -> None:
    """Score a range of past cutoffs to build risk history. Never raises alerts."""
    from worklist.agent.pipeline import backfill as run

    raise typer.Exit(run(from_, to, int(every.rstrip("d"))))


@app.command()
def nightly(
    cutoff: str = typer.Option(None, help="Process this cutoff instead of advancing the clock"),
    tenant: str = typer.Option(None, help="Tenant name or id (default: all)"),
    no_advance: bool = typer.Option(False, "--no-advance"),
    no_llm: bool = typer.Option(False, "--no-llm"),
) -> None:
    """Advance the sim clock once, then ingest, score, alert and draft for every tenant."""
    from worklist.agent.pipeline import nightly as run

    raise typer.Exit(run(cutoff=_date(cutoff), tenant=tenant, advance=not no_advance and cutoff is None,
                         use_llm=not no_llm))


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


if __name__ == "__main__":
    app()
