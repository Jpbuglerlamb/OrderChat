#app/routes/stripes.py
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Restaurant, User
from app.routes.auth_platform import get_current_platform_user
from app.services.storage import generate_download_url

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_latest_restaurant_for_user(db: Session, current_user: User | None) -> Restaurant | None:
    if not current_user:
        return None

    return (
        db.query(Restaurant)
        .filter(Restaurant.owner_user_id == current_user.id)
        .order_by(Restaurant.id.desc())
        .first()
    )


def get_qr_download_url(restaurant: Restaurant | None) -> str | None:
    if not restaurant or not restaurant.qr_code_path:
        return None

    try:
        return generate_download_url(restaurant.qr_code_path)
    except Exception:
        return None


@router.get("/success", response_class=HTMLResponse)
async def billing_success(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    restaurant = get_latest_restaurant_for_user(db, current_user)

    return templates.TemplateResponse(
        "business_onboarding_complete.html",
        {
            "request": request,
            "slug": restaurant.slug if restaurant else "",
            "qr_download_url": get_qr_download_url(restaurant),
            "current_user": current_user,
            "dashboard_url": f"/r/{restaurant.slug}/staff" if restaurant else "/business",
        },
    )


@router.get("/cancel", response_class=HTMLResponse)
async def billing_cancel(request: Request):
    return templates.TemplateResponse(
        "billing_cancel.html",
        {
            "request": request,
            "current_user": None,
            "dashboard_url": "/business",
        },
    )