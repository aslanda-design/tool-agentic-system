import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import container
from app.adapters.brokers.statement_files.generic import parse_generic_csv
from app.adapters.brokers.statement_files.myinvestor import parse_myinvestor_file
from app.adapters.persistence.session import get_db
from app.domain.errors import StatementParseError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["imports"])

PARSERS = {"generic": parse_generic_csv, "myinvestor": parse_myinvestor_file}

# Generous but bounded — a real statement export is a few hundred KB to a
# few MB; this just keeps a mistaken/huge upload from being parsed at all.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _parse(file_bytes: bytes, format: str):
    parser = PARSERS.get(format)
    if parser is None:
        raise HTTPException(status_code=400, detail=f"Unknown format: {format!r}. Available: {list(PARSERS)}")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    try:
        return parser(file_bytes)
    except StatementParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/preview")
async def preview_import(
    account_id: int, format: str = "generic", file: UploadFile = File(...), db: Session = Depends(get_db)
):
    statement = _parse(await file.read(), format)
    return container.build_csv_import_use_case(db).preview(statement, account_id)


@router.post("/commit")
async def commit_import(
    account_id: int, format: str = "generic", file: UploadFile = File(...), db: Session = Depends(get_db)
):
    statement = _parse(await file.read(), format)
    if statement.unmapped_columns:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not map required columns: {', '.join(statement.unmapped_columns)}. "
                f"Headers found in the file: {', '.join(statement.detected_headers) or '(none)'}."
            ),
        )
    try:
        result = container.build_csv_import_use_case(db).commit(statement, account_id)
    except Exception:
        db.rollback()
        logger.exception("Import commit for account_id=%s (format=%s) raised an unexpected error", account_id, format)
        raise
    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    return result
