import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.scheduler import build_scheduler, gap_fill_snapshots_on_startup
from app.api.routes import (
    accounts,
    analytics,
    assets,
    chat,
    health,
    imports,
    manual,
    market_data,
    notes,
    portfolio,
    positions,
    quant,
    resolutions,
    sync,
)

logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")


def _configure_logging() -> None:
    # Alembic's migrations/env.py calls logging.config.fileConfig() (to read
    # alembic.ini's [loggers] section), which defaults to
    # disable_existing_loggers=True — that silently disables every one of our
    # `logging.getLogger(__name__)` loggers and resets the root level. Since
    # _run_migrations() runs first in lifespan, our own config must run AFTER
    # it, not before, or it gets clobbered before the app ever serves a request.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s", force=True
    )
    for name, log_obj in logging.Logger.manager.loggerDict.items():
        if name.startswith("app") and isinstance(log_obj, logging.Logger):
            log_obj.disabled = False


scheduler = build_scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_migrations()
    _configure_logging()
    gap_fill_snapshots_on_startup()
    # Deliberately NOT running the security resolver here, unlike the
    # snapshot gap-fill above: the resolver makes real outbound OpenFIGI/
    # Yahoo calls and can permanently change a real asset's mapping — doing
    # that on every process boot would also mean every `TestClient(app)` in
    # the test suite (test_health.py, test_imports_route.py) does it too,
    # against whatever database DATABASE_URL happens to point at. It runs
    # instead via the scheduled interval job and right after an import/sync
    # actually adds new assets (see adapters/scheduler.py's run_resolver_job
    # and its BackgroundTasks callers in api/routes/imports.py and sync.py).
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Investment Tracker", version="0.1.0", lifespan=lifespan)

# Local-only app: frontend runs on localhost, either via Docker (port 80) or
# the Vite dev server (port 5173) for the manual/non-Docker path.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(portfolio.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(assets.router, prefix="/api")
app.include_router(accounts.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(manual.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(market_data.router, prefix="/api")
app.include_router(resolutions.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(quant.router, prefix="/api")
