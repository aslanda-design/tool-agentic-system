from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.api.schemas import AssetUpdateRequest, MapAssetRequest
from app.domain.errors import AssetConflictError, AssetNotFoundError, ListingNotFoundError
from app.domain.listings import ResolutionStatus

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/search")
def search_assets(q: str, db: Session = Depends(get_db)):
    results = container.build_search_assets_use_case(db).execute(q)
    db.commit()
    return results


@router.get("/needs-mapping")
def list_needs_mapping(db: Session = Depends(get_db)):
    return container.build_map_asset_use_case(db).list_unmapped()


@router.get("/{asset_id}/map-suggestions")
def suggest_asset_mapping(asset_id: int, db: Session = Depends(get_db)):
    return container.build_map_asset_use_case(db).suggest_tickers(asset_id)


@router.post("/{asset_id}/map")
def map_asset(asset_id: int, request: MapAssetRequest, db: Session = Depends(get_db)):
    try:
        container.build_map_asset_use_case(db).resolve(asset_id, request.yfinance_symbol)
    except ListingNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AssetConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    # Pull quotes/history for the newly-mapped ticker right away rather than
    # making the user wait for the next scheduled refresh (up to 15 min).
    container.build_refresh_market_data_use_case(db).refresh_all()
    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    return {"status": "ok"}


@router.post("/{asset_id}/resolve")
def resolve_asset_now(asset_id: int, db: Session = Depends(get_db)):
    """Run the security resolver (application/resolve_security.py) for one
    asset right now, rather than waiting for the background job (see
    adapters/scheduler.py). An explicit, infrequent user action — makes
    real OpenFIGI/Yahoo calls, which is why this isn't triggered from a
    plain page load. Refreshes market data and rebuilds snapshots
    immediately when the rules auto-accept a listing."""
    if container.asset_repo(db).get(asset_id) is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    resolution = container.build_resolve_security_use_case(db).resolve_asset(asset_id)
    db.commit()
    if resolution.status is ResolutionStatus.AUTO_ACCEPTED:
        container.build_refresh_market_data_use_case(db).refresh_all()
        db.commit()
        container.build_snapshots_use_case(db).execute()
        db.commit()
    return resolution


@router.get("/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    detail = container.build_query_asset_use_case(db).get_detail(asset_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return detail


@router.patch("/{asset_id}")
def update_asset(asset_id: int, request: AssetUpdateRequest, db: Session = Depends(get_db)):
    try:
        asset = container.build_manual_entry_use_case(db).update_asset(
            asset_id, request.symbol, request.name, request.isin
        )
    except AssetNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssetConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return asset


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    """Permanently removes the asset and every holding/transaction recorded
    against it, across every account — the escape hatch for a mis-mapped
    import (e.g. resolved to the wrong instrument, or landed in the wrong
    account). Irreversible."""
    try:
        result = container.build_manual_entry_use_case(db).delete_asset(asset_id)
    except AssetNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    return result


@router.get("/{asset_id}/history")
def get_asset_history(asset_id: int, range: str = "1Y", db: Session = Depends(get_db)):
    return container.build_asset_chart_use_case(db).get_daily(asset_id, range)


@router.get("/{asset_id}/intraday")
def get_asset_intraday(asset_id: int, granularity: str = "1h", db: Session = Depends(get_db)):
    return container.build_asset_chart_use_case(db).get_intraday(asset_id, granularity)
