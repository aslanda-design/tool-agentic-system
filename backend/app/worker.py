"""Background-jobs entrypoint: `python -m app.worker`.

Runs the scheduler (market-data refresh, snapshots, security resolver) in its
own process so API replicas stay stateless: N API replicas must not mean N
copies of every job (docs/saas/01 §4.1). A Postgres advisory lock makes an
accidental second worker exit instead of duplicating work.

The API runs the scheduler itself only when RUN_SCHEDULER_IN_API=true, the
default for single-container local development.
"""

from __future__ import annotations

import logging
import sys
import threading

from app.adapters.persistence import locks
from app.adapters.persistence.session import check_rls_enforced
from app.adapters.scheduler import build_scheduler, gap_fill_snapshots_on_startup

logger = logging.getLogger("app.worker")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s", force=True
    )
    check_rls_enforced()
    lock = locks.try_acquire(locks.SCHEDULER_LOCK_KEY)
    if lock is None:
        logger.warning("Another worker already holds the scheduler lock; exiting.")
        return 0
    try:
        gap_fill_snapshots_on_startup()
        scheduler = build_scheduler()
        scheduler.start()
        logger.info("Worker started; scheduler running.")
        threading.Event().wait()  # block until the process is stopped
    except KeyboardInterrupt:
        pass
    finally:
        lock.close()  # releases the advisory lock
    return 0


if __name__ == "__main__":
    sys.exit(main())
