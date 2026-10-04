import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.persistence.session import check_rls_enforced
from app.adapters.scheduler import build_scheduler, gap_fill_snapshots_on_startup
from app.api.routes import (
    accounts,
    analytics,
    assets,
    auth,
    chat,
    health,
    imports,
    manual,
    market_data,
    market_intel,
    notes,
    portfolio,
    positions,
    quant,
    resolutions,
    sync,
)
from app.config import settings

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
    if settings.auth_mode == "dev" and settings.app_env != "dev":
        raise RuntimeError("auth_mode='dev' (no login) is only allowed when app_env='dev'")
    if settings.auth_mode == "oidc":
        if len(settings.session_secret) < 32:
            raise RuntimeError("SESSION_SECRET must be set (>= 32 chars) when AUTH_MODE=oidc")
        if not settings.oidc_client_secret:
            raise RuntimeError("OIDC_CLIENT_SECRET must be set when AUTH_MODE=oidc")
        if settings.app_env != "dev" and not settings.app_base_url.startswith("https://"):
            raise RuntimeError("APP_BASE_URL must be https outside dev")
    check_rls_enforced()
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
app.include_router(auth.router, prefix="/api")
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
app.include_router(market_intel.router, prefix="/api")
