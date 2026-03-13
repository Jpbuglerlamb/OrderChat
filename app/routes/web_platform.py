from pathlib import Path
import json
import os
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.routes.auth_platform import set_session_cookie
from app.db import get_db
from app.models import User, Restaurant
from app.security.auth import hash_password
from app.services.menu_ingest import ingest_menu_file_to_dataset
from app.services.qr_service import build_restaurant_public_url, generate_qr_png_bytes
from app.services.storage import upload_file_bytes, generate_download_url
from app.ordering.menu_store import clear_menu_cache
from app.services.storage import get_json_file
router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "restaurant"


def unique_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    counter = 2

    while db.query(Restaurant).filter(Restaurant.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    return slug


def build_s3_menu_key(slug: str, filename: str) -> str:
    safe_name = (filename or "menu_upload.bin").replace(" ", "_")
    return f"restaurants/{slug}/uploads/{uuid4()}_{safe_name}"


def render_signup_error(
    request: Request,
    plan: str,
    message: str,
    status_code: int = 400,
):
    return templates.TemplateResponse(
        "business_signup.html",
        {
            "request": request,
            "error": message,
            "plan": plan,
        },
        status_code=status_code,
    )


def validate_menu_dataset(menu_dataset: dict) -> list[str]:
    errors: list[str] = []

    categories = menu_dataset.get("categories") or []
    items = menu_dataset.get("items") or []

    if not isinstance(categories, list) or not categories:
        errors.append("No valid categories were extracted from the uploaded menu.")

    if not isinstance(items, list) or not items:
        errors.append("No valid menu items were extracted from the uploaded menu.")

    return errors


# --------------------------------
# Basic Pages
# --------------------------------

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@router.get("/customer", response_class=HTMLResponse)
def customer_page(request: Request):
    return templates.TemplateResponse("customer_home.html", {"request": request})


@router.get("/business", response_class=HTMLResponse)
def business_page(request: Request):
    return templates.TemplateResponse("business_home.html", {"request": request})


# --------------------------------
# Pricing Page
# --------------------------------

@router.get("/business/pricing", response_class=HTMLResponse)
def business_pricing_page(request: Request):
    return templates.TemplateResponse(
        "business_pricing.html",
        {"request": request},
    )


# --------------------------------
# Signup Page
# --------------------------------

@router.get("/business/signup", response_class=HTMLResponse)
def business_signup_page(request: Request, plan: str = ""):
    return templates.TemplateResponse(
        "business_signup.html",
        {
            "request": request,
            "error": None,
            "plan": plan,
        },
    )


# --------------------------------
# Signup Submit
# --------------------------------

@router.post("/business/signup", response_class=HTMLResponse)
async def business_signup_submit(
    request: Request,
    name: str = Form(...),
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    phone: str = Form(""),
    address: str = Form(...),
    opening_hours: str = Form(...),
    plan: str = Form("starter"),
    menu_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        return render_signup_error(
            request,
            plan,
            "An account with that email already exists.",
            status_code=400,
        )

    if not menu_file:
        return render_signup_error(
            request,
            plan,
            "Please upload a menu file.",
            status_code=400,
        )

    original_filename = (menu_file.filename or "").strip() or "menu_upload.bin"
    file_bytes = await menu_file.read()

    if not file_bytes:
        return render_signup_error(
            request,
            plan,
            "The uploaded menu file was empty.",
            status_code=400,
        )

    base_slug = slugify(business_name)
    slug = unique_slug(db, base_slug)

    raw_menu_s3_key = build_s3_menu_key(slug, original_filename)

    try:
        upload_file_bytes(
            data=file_bytes,
            key=raw_menu_s3_key,
            content_type=menu_file.content_type or "application/octet-stream",
        )
    except Exception as e:
        return render_signup_error(
            request,
            plan,
            f"Menu upload failed: {str(e)}",
            status_code=500,
        )

    try:
        menu_dataset = ingest_menu_file_to_dataset(
            file_bytes=file_bytes,
            filename=original_filename,
            business_name=business_name,
            email=email,
            phone=phone or "",
            address=address,
            opening_hours=opening_hours,
            currency="GBP",
            pickup_only=True,
        )
    except Exception as e:
        return render_signup_error(
            request,
            plan,
            f"Menu could not be parsed: {str(e)}",
            status_code=400,
        )

    validation_errors = validate_menu_dataset(menu_dataset)
    if validation_errors:
        return render_signup_error(
            request,
            plan,
            " ".join(validation_errors),
            status_code=400,
        )

    menu_json_bytes = json.dumps(
        menu_dataset,
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")

    menu_json_s3_key = f"restaurants/{slug}/menu.json"

    try:
        upload_file_bytes(
            data=menu_json_bytes,
            key=menu_json_s3_key,
            content_type="application/json",
        )
    except Exception as e:
        return render_signup_error(
            request,
            plan,
            f"Processed menu could not be saved: {str(e)}",
            status_code=500,
        )

    clear_menu_cache(slug)

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not public_base_url:
        return render_signup_error(
            request,
            plan,
            "PUBLIC_BASE_URL is missing from environment variables.",
            status_code=500,
        )

    try:
        restaurant_public_url = build_restaurant_public_url(public_base_url, slug)
        qr_png_bytes = generate_qr_png_bytes(restaurant_public_url)
    except Exception as e:
        return render_signup_error(
            request,
            plan,
            f"QR code generation failed: {str(e)}",
            status_code=500,
        )

    qr_s3_key = f"restaurants/{slug}/qr.png"

    try:
        upload_file_bytes(
            data=qr_png_bytes,
            key=qr_s3_key,
            content_type="image/png",
        )
    except Exception as e:
        return render_signup_error(
            request,
            plan,
            f"QR code upload failed: {str(e)}",
            status_code=500,
        )

    try:
        user = User(
            name=name,
            email=email,
            phone=phone or None,
            address=address,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()

        restaurant = Restaurant(
            owner_user_id=user.id,
            name=business_name,
            slug=slug,
            phone=phone or "",
            address=address,
            opening_hours=opening_hours,
            menu_upload_path=raw_menu_s3_key,
            menu_json_path=menu_json_s3_key,
            qr_code_path=qr_s3_key,
        )

        db.add(restaurant)
        db.commit()

    except Exception as e:
        db.rollback()
        return render_signup_error(
            request,
            plan,
            f"Account setup failed while saving your restaurant: {str(e)}",
            status_code=500,
        )

    response = RedirectResponse(
        url=f"/business/onboarding-complete?slug={slug}",
        status_code=303,
    )
    set_session_cookie(response, email)
    return response


# --------------------------------
# Onboarding Complete
# --------------------------------

@router.get("/business/onboarding-complete", response_class=HTMLResponse)
def onboarding_complete(
    request: Request,
    slug: str = "",
    db: Session = Depends(get_db),
):
    restaurant = db.query(Restaurant).filter(Restaurant.slug == slug).first()

    qr_download_url = None
    if restaurant and restaurant.qr_code_path:
        try:
            qr_download_url = generate_download_url(restaurant.qr_code_path)
        except Exception:
            qr_download_url = None

    return templates.TemplateResponse(
        "business_onboarding_complete.html",
        {
            "request": request,
            "slug": slug,
            "restaurant": restaurant,
            "qr_download_url": qr_download_url,
        },
    )


# --------------------------------
# Debug
# --------------------------------

@router.get("/debug/users")
def debug_users(db: Session = Depends(get_db)):
    rows = db.query(User).all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
        }
        for u in rows
    ]


@router.get("/debug/restaurants")
def debug_restaurants(db: Session = Depends(get_db)):
    rows = db.query(Restaurant).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
            "menu_json_path": r.menu_json_path,
            "qr_code_path": r.qr_code_path,
        }
        for r in rows
    ]

@router.get("/debug/restaurant-menu/{slug}")
def debug_restaurant_menu(slug: str, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(Restaurant.slug == slug).first()
    if not restaurant:
        return {"ok": False, "error": "Restaurant not found"}

    if not restaurant.menu_json_path:
        return {"ok": False, "error": "Restaurant has no menu_json_path"}

    try:
        data = get_json_file(restaurant.menu_json_path)
    except Exception as e:
        return {"ok": False, "error": f"Failed to load menu JSON: {str(e)}"}

    return {
        "ok": True,
        "slug": slug,
        "menu_json_path": restaurant.menu_json_path,
        "category_names": [c.get("name") for c in data.get("categories", [])],
        "item_names": [i.get("name") for i in data.get("items", [])[:20]],
        "raw": data,
    }