from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.api.schemas import ManualAccountRequest

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("")
def list_accounts(db: Session = Depends(get_db)):
    return container.portfolio_repo(db).list_accounts()


@router.post("")
def create_manual_account(request: ManualAccountRequest, db: Session = Depends(get_db)):
    account = container.portfolio_repo(db).get_or_create_account(
        request.broker_key, request.name, request.name, request.currency, "manual"
    )
    db.commit()
    return account
