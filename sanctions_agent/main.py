"""
Sanctions Agent – main entry point.

Daily sync at RUN_HOUR_UTC:RUN_MINUTE_UTC (default 18:00 UTC).
Manual trigger:  python -m sanctions_agent.main --run-now

Pipeline per run:
  1. Fetch UN / OFAC / EU XML lists (with retry).
  2. Parse → SanctionEntity objects.
  3. Compute delta (read-only) → stats (new / modified / unchanged / removed).
  4. Wipe & reload by source: for each source that returned entities, delete
     all rows in DB for that source_name, then insert the fresh batch.
  5. Commit hashes to sanctions_sync_meta for next run's delta.
  6. Notify via Brevo email (and Slack if configured).
"""
import argparse
import logging
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import LOG_LEVEL, RUN_HOUR_UTC, RUN_MINUTE_UTC
from .notifier import notify_sync_complete, notify_sync_failed
from .delta import commit_hashes, compute_delta, ensure_delta_schema
from .upsert import ensure_schema, wipe_and_insert
from . import eu, ofac, un

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("sanctions_agent")


def run_sync():
    start = time.monotonic()
    logger.info("══════════════ Sanctions sync START ══════════════")

    fetch_errors = []
    all_entities = []

    sources = [
        ("UN",   un.fetch),
        ("OFAC", ofac.fetch),
        ("EU",   eu.fetch),
    ]
    for name, fetch_fn in sources:
        try:
            entities = fetch_fn()
            if not entities:
                msg = f"{name}: 0 entities returned (possible fetch failure)"
                logger.warning(msg)
                fetch_errors.append(msg)
                notify_sync_failed(name, "0 entities – check logs")
            else:
                logger.info("%s – %d entities fetched", name, len(entities))
                all_entities.extend(entities)
        except Exception as exc:
            msg = f"{name}: unhandled exception – {exc}"
            logger.error(msg)
            fetch_errors.append(msg)
            notify_sync_failed(name, str(exc))

    logger.info("Total entities fetched: %d", len(all_entities))

    if not all_entities:
        logger.error("No entities at all – aborting sync")
        notify_sync_complete(
            delta_stats={},
            upsert_stats={"errors": 1, "inserted": 0, "sources_wiped": 0},
            errors=fetch_errors,
            duration_seconds=time.monotonic() - start,
        )
        return

    # Read-only delta for the report (does not drive the write).
    delta = compute_delta(all_entities)
    logger.info(
        "Delta (stats only) – new=%d modified=%d unchanged=%d removed=%d",
        delta.stats.get("new", 0),
        delta.stats.get("modified", 0),
        delta.stats.get("unchanged", 0),
        delta.stats.get("removed", 0),
    )

    # Wipe & reload each source that returned data.
    upsert_stats = wipe_and_insert(all_entities)
    commit_hashes(all_entities)

    duration = time.monotonic() - start
    logger.info(
        "══ DONE (%.1fs) inserted=%d sources_wiped=%d errors=%d ══",
        duration,
        upsert_stats.get("inserted", 0),
        upsert_stats.get("sources_wiped", 0),
        upsert_stats.get("errors", 0) + len(fetch_errors),
    )

    notify_sync_complete(
        delta_stats=delta.stats,
        upsert_stats=upsert_stats,
        errors=fetch_errors,
        duration_seconds=duration,
    )


def main():
    parser = argparse.ArgumentParser(description="Sanctions Agent")
    parser.add_argument("--run-now", action="store_true",
                        help="Run a sync immediately then exit")
    args = parser.parse_args()

    logger.info("Sanctions Agent starting – checking DB schema …")
    ensure_schema()
    ensure_delta_schema()

    if args.run_now:
        run_sync()
        return

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_sync,
        trigger=CronTrigger(hour=RUN_HOUR_UTC, minute=RUN_MINUTE_UTC, timezone="UTC"),
        id="sanctions_sync",
        name="Daily sanctions sync",
        max_instances=1,
        misfire_grace_time=3600,
    )
    logger.info(
        "Scheduler armed – daily sync at %02d:%02d UTC", RUN_HOUR_UTC, RUN_MINUTE_UTC
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Agent stopped.")


if __name__ == "__main__":
    main()
