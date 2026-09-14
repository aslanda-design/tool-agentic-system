from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.api.schemas import ManualHoldingRequest, ManualTransactionRequest, ManualTransactionUpdateRequest
from app.domain.models import TransactionType

router = APIRouter(prefix="/manual", tags=["manual"])


@router.get("/opening-balance-suggestion")
def suggest_opening_balance(account_id: int, asset_id: int, db: Session = Depends(get_db)):
    return container.build_suggest_opening_balance_use_case(db).execute(account_id, asset_id)


@router.post("/holdings")
def upsert_holding(request: ManualHoldingRequest, db: Session = Depends(get_db)):
    container.build_manual_entry_use_case(db).upsert_holding(
        request.account_id,
        request.symbol,
        request.name,
        request.currency,
        request.quantity,
        request.avg_cost_price,
        request.isin,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/transactions")
def add_transaction(request: ManualTransactionRequest, db: Session = Depends(get_db)):
    txn = container.build_manual_entry_use_case(db).add_transaction(
        request.account_id,
        request.symbol,
        TransactionType(request.type),
        request.quantity,
        request.price,
        request.fees,
        request.currency,
        request.executed_at,
        request.note,
    )
    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    return txn


@router.patch("/transactions/{transaction_id}")
def update_transaction(transaction_id: int, request: ManualTransactionUpdateRequest, db: Session = Depends(get_db)):
    fields = {k: v for k, v in request.model_dump().items() if v is not None}
    container.build_manual_entry_use_case(db).update_transaction(transaction_id, **fields)
    db.commit()
    return {"status": "ok"}


@router.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    container.build_manual_entry_use_case(db).delete_transaction(transaction_id)
    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    return {"status": "ok"}
