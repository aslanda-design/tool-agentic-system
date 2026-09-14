import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.domain.errors import BrokerConnectionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/{broker_key}")
def sync_broker(broker_key: str, db: Session = Depends(get_db)):
    logger.info("Sync requested for broker_key=%s", broker_key)
    try:
        use_case = container.build_sync_broker_use_case(db, broker_key)
    except ValueError as exc:
        logger.warning("Sync failed: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        result = use_case.execute()
    except BrokerConnectionError as exc:
        db.rollback()
        logger.warning("Sync for broker_key=%s failed: %s", broker_key, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        logger.exception("Sync for broker_key=%s raised an unexpected error", broker_key)
        raise

    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    logger.info("Sync for broker_key=%s complete: %s", broker_key, result)
    return result


@router.post("/{account_id}/flex-import")
def import_flex_history(account_id: int, db: Session = Depends(get_db)):
    logger.info("Flex history import requested for account_id=%s", account_id)
    account = container.portfolio_repo(db).get_account(account_id)
    if account is None:
        logger.warning("Flex history import failed: account_id=%s not found", account_id)
        raise HTTPException(status_code=404, detail="Account not found")

    use_case = container.build_flex_import_use_case(db, account_id, account.external_id)
    try:
        result = use_case.execute()
    except BrokerConnectionError as exc:
        db.rollback()
        logger.warning("Flex history import for account_id=%s failed: %s", account_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        logger.exception("Flex history import for account_id=%s raised an unexpected error", account_id)
        raise

    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    logger.info("Flex history import for account_id=%s complete: %s", account_id, result)
    return result


@router.get("/status")
def sync_status(db: Session = Depends(get_db)):
    repo = container.portfolio_repo(db)
    statuses = []
    for account in repo.list_accounts():
        transactions = repo.list_transactions(account_id=account.id)
        statuses.append(
            {
                "account_id": account.id,
                "broker_key": account.broker_key,
                "source": account.source,
                "last_transaction_date": transactions[-1].trade_date if transactions else None,
                "earliest_transaction_date": repo.earliest_transaction_date(account_id=account.id),
            }
        )
    return statuses
