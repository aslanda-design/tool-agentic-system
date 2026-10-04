"""A second scheduler must be harmless: only one process holds the lock."""

from app.adapters.persistence import locks


def test_only_one_process_can_hold_the_scheduler_lock():
    first = locks.try_acquire(locks.SCHEDULER_LOCK_KEY)
    assert first is not None
    try:
        assert locks.try_acquire(locks.SCHEDULER_LOCK_KEY) is None  # second worker backs off
    finally:
        first.close()
    again = locks.try_acquire(locks.SCHEDULER_LOCK_KEY)  # released when the holder goes away
    assert again is not None
    again.close()
