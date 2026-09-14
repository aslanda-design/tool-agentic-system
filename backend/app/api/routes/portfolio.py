from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    return container.build_query_portfolio_use_case(db).get_summary()


@router.get("/history")
def get_history(range: str = "1Y", asset_ids: str | None = None, db: Session = Depends(get_db)):
    ids = [int(x) for x in asset_ids.split(",") if x.strip()] if asset_ids else None
    return container.build_query_portfolio_use_case(db).get_history(range, ids)


@router.get("/allocation")
def get_allocation(by: str = "asset_class", db: Session = Depends(get_db)):
    return container.build_query_portfolio_use_case(db).get_allocation(by)
