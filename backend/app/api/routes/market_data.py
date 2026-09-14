from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db

router = APIRouter(tags=["market-data"])


@router.post("/market-data/refresh")
def refresh_market_data(db: Session = Depends(get_db)):
    result = container.build_refresh_market_data_use_case(db).refresh_all()
    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    return result


@router.post("/snapshots/rebuild")
def rebuild_snapshots(db: Session = Depends(get_db)):
    days = container.build_snapshots_use_case(db).execute()
    db.commit()
    return {"days_written": days}
