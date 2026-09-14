from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("")
def list_positions(account_id: int | None = None, db: Session = Depends(get_db)):
    return container.build_query_portfolio_use_case(db).list_positions(account_id)


@router.get("/{asset_id}")
def get_position(asset_id: int, db: Session = Depends(get_db)):
    detail = container.build_query_asset_use_case(db).get_detail(asset_id)
    if detail is None or detail.position is None:
        raise HTTPException(status_code=404, detail="No position for this asset")
    transactions = container.portfolio_repo(db).list_transactions(asset_id=asset_id)
    return {"position": detail.position, "transactions": transactions}
