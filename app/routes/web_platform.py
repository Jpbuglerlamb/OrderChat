from pathlib import Path
import os
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Restaurant, User
from app.ordering.menu_store import clear_menu_cache
from app.routes.auth_platform import get_current_platform_user, set_session_cookie
from app.security.auth import hash_password
from app.services.menu_ingest import ingest_menu_file_to_dataset
from app.services.qr_service import build_restaurant_public_url, generate_qr_png_bytes
from app.services.storage import (
    upload_file_bytes,
    generate_download_url,
    get_json_file,
    save_json_file,
)

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# --------------------------------
# Helpers
# --------------------------------

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


def get_latest_restaurant_for_user(db: Session, current_user: User | None) -> Restaurant | None:
    if not current_user:
        return None

    return (
        db.query(Restaurant)
        .filter(Restaurant.owner_user_id == current_user.id)
        .order_by(Restaurant.id.desc())
        .first()
    )


def build_dashboard_url_for_user(db: Session, current_user: User | None) -> str:
    restaurant = get_latest_restaurant_for_user(db, current_user)
    if restaurant:
        return f"/r/{restaurant.slug}/staff"
    return "/business"


def get_qr_download_url(restaurant: Restaurant | None) -> str | None:
    if not restaurant or not restaurant.qr_code_path:
        return None

    try:
        return generate_download_url(restaurant.qr_code_path)
    except Exception:
        return None


def get_public_order_url(restaurant: Restaurant | None) -> str | None:
    if not restaurant:
        return None

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not public_base_url:
        return None

    try:
        return build_restaurant_public_url(public_base_url, restaurant.slug)
    except Exception:
        return None


def normalize_menu_categories(raw_categories: list) -> list[dict]:
    normalized: list[dict] = []

    for c in raw_categories or []:
        if isinstance(c, dict):
            cid = str(c.get("id") or c.get("name") or "").strip()
            cname = str(c.get("name") or cid or "Uncategorised").strip()
        else:
            cid = str(c or "").strip()
            cname = cid.replace("-", " ").title() if cid else "Uncategorised"

        if cid:
            normalized.append(
                {
                    "id": cid,
                    "name": cname,
                }
            )

    return normalized


def build_items_by_category(menu_data: dict) -> tuple[list[dict], dict]:
    raw_categories = menu_data.get("categories") or []
    items = menu_data.get("items") or []

    categories = normalize_menu_categories(raw_categories)

    category_map: dict[str, str] = {}
    for c in categories:
        category_map[c["id"]] = c["name"]
        category_map[c["name"]] = c["name"]

    items_by_category: dict[str, list] = {}
    for item in items:
        category_key = str(item.get("category") or item.get("category_id") or "").strip()
        category_name = category_map.get(category_key) or category_key.replace("-", " ").title() or "Uncategorised"

        if "available" not in item:
            item["available"] = True

        items_by_category.setdefault(category_name, []).append(item)

    return categories, items_by_category


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
            "current_user": None,
            "dashboard_url": "/business",
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


def render_business_menu_page(
    request: Request,
    current_user: User,
    dashboard_url: str,
    restaurant: Restaurant,
    menu_data: dict | None,
    error: str | None = None,
    success: str | None = None,
    status_code: int = 200,
):
    categories: list[dict] = []
    items_by_category: dict = {}

    if menu_data:
        categories, items_by_category = build_items_by_category(menu_data)

    return templates.TemplateResponse(
        "business_menu.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
            "restaurant": restaurant,
            "menu_data": menu_data,
            "items_by_category": items_by_category,
            "categories": categories,
            "error": error,
            "success": success,
        },
        status_code=status_code,
    )


# --------------------------------
# Basic Pages
# --------------------------------

@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
        },
    )


@router.get("/customer", response_class=HTMLResponse)
def customer_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    return templates.TemplateResponse(
        "customer_home.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
        },
    )


@router.get("/business", response_class=HTMLResponse)
def business_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    if current_user and dashboard_url != "/business":
        return RedirectResponse(url=dashboard_url, status_code=302)

    return templates.TemplateResponse(
        "business_home.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
        },
    )


# --------------------------------
# Settings
# --------------------------------

@router.get("/business/settings", response_class=HTMLResponse)
def business_settings_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    if not current_user:
        return RedirectResponse(url="/business/login?next=/business/settings", status_code=302)

    restaurant = get_latest_restaurant_for_user(db, current_user)
    if not restaurant:
        return RedirectResponse(url="/business/signup", status_code=302)

    return templates.TemplateResponse(
        "business_settings.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
            "restaurant": restaurant,
            "qr_download_url": get_qr_download_url(restaurant),
        },
    )


@router.post("/business/settings", response_class=HTMLResponse)
def business_settings_submit(
    request: Request,
    business_name: str = Form(...),
    phone: str = Form(""),
    address: str = Form(...),
    opening_hours: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    if not current_user:
        return RedirectResponse(url="/business/login?next=/business/settings", status_code=302)

    restaurant = get_latest_restaurant_for_user(db, current_user)
    if not restaurant:
        return RedirectResponse(url="/business/signup", status_code=302)

    restaurant.name = business_name.strip()
    restaurant.phone = phone.strip()
    restaurant.address = address.strip()
    restaurant.opening_hours = opening_hours.strip()

    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)

    return templates.TemplateResponse(
        "business_settings.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
            "restaurant": restaurant,
            "qr_download_url": get_qr_download_url(restaurant),
            "success": "Settings updated successfully.",
        },
    )


# --------------------------------
# Menu Editor
# --------------------------------

@router.get("/business/menu", response_class=HTMLResponse)
def business_menu_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    if not current_user:
        return RedirectResponse(url="/business/login?next=/business/menu", status_code=302)

    restaurant = get_latest_restaurant_for_user(db, current_user)
    if not restaurant:
        return RedirectResponse(url="/business/signup", status_code=302)

    if not restaurant.menu_json_path:
        return RedirectResponse(url="/business/settings", status_code=302)

    try:
        menu_data = get_json_file(restaurant.menu_json_path)
    except Exception as e:
        return render_business_menu_page(
            request=request,
            current_user=current_user,
            dashboard_url=dashboard_url,
            restaurant=restaurant,
            menu_data=None,
            error=f"Could not load menu: {str(e)}",
            status_code=500,
        )

    return render_business_menu_page(
        request=request,
        current_user=current_user,
        dashboard_url=dashboard_url,
        restaurant=restaurant,
        menu_data=menu_data,
    )


@router.post("/business/menu", response_class=HTMLResponse)
async def business_menu_submit(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    if not current_user:
        return RedirectResponse(url="/business/login?next=/business/menu", status_code=302)

    restaurant = get_latest_restaurant_for_user(db, current_user)
    if not restaurant:
        return RedirectResponse(url="/business/signup", status_code=302)

    if not restaurant.menu_json_path:
        return RedirectResponse(url="/business/settings", status_code=302)

    try:
        menu_data = get_json_file(restaurant.menu_json_path)
    except Exception as e:
        return render_business_menu_page(
            request=request,
            current_user=current_user,
            dashboard_url=dashboard_url,
            restaurant=restaurant,
            menu_data=None,
            error=f"Could not load menu: {str(e)}",
            status_code=500,
        )

    form = await request.form()
    action = str(form.get("action") or "save").strip().lower()

    raw_categories = menu_data.get("categories") or []
    items = menu_data.get("items") or []

    def render_with(
        message_success: str | None = None,
        message_error: str | None = None,
        status_code: int = 200,
    ):
        return render_business_menu_page(
            request=request,
            current_user=current_user,
            dashboard_url=dashboard_url,
            restaurant=restaurant,
            menu_data=menu_data,
            error=message_error,
            success=message_success,
            status_code=status_code,
        )

    if action == "add_category":
        new_category_name = str(form.get("new_category_name") or "").strip()
        if not new_category_name:
            return render_with(message_error="Please enter a category name.", status_code=400)

        normalized_categories = normalize_menu_categories(raw_categories)
        existing_names = {c["name"].strip().lower() for c in normalized_categories}
        existing_ids = {c["id"].strip().lower() for c in normalized_categories}

        pretty_name = new_category_name.strip()
        new_category_id = slugify(pretty_name)

        if pretty_name.lower() in existing_names or new_category_id.lower() in existing_ids:
            return render_with(message_error="That category already exists.", status_code=400)

        base_id = new_category_id
        counter = 2
        while new_category_id.lower() in existing_ids:
            new_category_id = f"{base_id}-{counter}"
            counter += 1

        # Keep parser-compatible category format: list[str]
        raw_categories.append(new_category_id)
        menu_data["categories"] = raw_categories

        try:
            save_json_file(restaurant.menu_json_path, menu_data)
            clear_menu_cache(restaurant.slug)
        except Exception as e:
            return render_with(message_error=f"Could not save category: {str(e)}", status_code=500)

        return render_with(message_success="Category added successfully.")

    if action == "add_item":
        category_id = str(form.get("category_id") or "").strip()
        item_name = str(form.get("new_item_name") or "").strip()
        item_price_raw = str(form.get("new_item_price") or "").strip()

        if not category_id:
            return render_with(message_error="Missing category.", status_code=400)

        if not item_name:
            return render_with(message_error="Please enter an item name.", status_code=400)

        try:
            item_price = round(float(item_price_raw), 2)
        except Exception:
            return render_with(message_error="Please enter a valid item price.", status_code=400)

        normalized_categories = normalize_menu_categories(raw_categories)
        category_exists = any(c["id"] == category_id for c in normalized_categories)
        if not category_exists:
            return render_with(message_error="Selected category does not exist.", status_code=400)

        new_item_id = slugify(item_name)
        existing_item_ids = {str(i.get("id") or "").strip().lower() for i in items}

        base_item_id = new_item_id
        counter = 2
        while new_item_id.lower() in existing_item_ids:
            new_item_id = f"{base_item_id}-{counter}"
            counter += 1

        items.append(
            {
                "id": new_item_id,
                "name": item_name,
                "base_price": item_price,
                "category_id": category_id,
                "available": True,
            }
        )
        menu_data["items"] = items

        try:
            save_json_file(restaurant.menu_json_path, menu_data)
            clear_menu_cache(restaurant.slug)
        except Exception as e:
            return render_with(message_error=f"Could not save new item: {str(e)}", status_code=500)

        return render_with(message_success="Item added successfully.")

    # Default action: save edited existing items
    updated_items = []

    for idx, item in enumerate(items):
        item_id = str(form.get(f"item_id_{idx}") or "").strip()
        original_id = str(item.get("id") or "").strip()

        if item_id != original_id:
            updated_items.append(item)
            continue

        new_name = str(form.get(f"item_name_{idx}") or item.get("name") or "").strip()
        new_price_raw = str(form.get(f"item_price_{idx}") or item.get("base_price") or "0").strip()
        new_available = form.get(f"item_available_{idx}") == "on"

        try:
            new_price = float(new_price_raw)
        except Exception:
            new_price = float(item.get("base_price") or 0.0)

        item["name"] = new_name or str(item.get("name") or "Unnamed item")
        item["base_price"] = round(new_price, 2)
        item["available"] = new_available

        updated_items.append(item)

    menu_data["items"] = updated_items

    try:
        save_json_file(restaurant.menu_json_path, menu_data)
        clear_menu_cache(restaurant.slug)
    except Exception as e:
        return render_with(message_error=f"Could not save menu: {str(e)}", status_code=500)

    return render_with(message_success="Menu updated successfully.")


@router.post("/business/menu/upload")
async def business_menu_upload(
    request: Request,
    menu_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_platform_user(request, db)

    if not current_user:
        return RedirectResponse(url="/business/login?next=/business/menu", status_code=302)

    restaurant = get_latest_restaurant_for_user(db, current_user)
    if not restaurant:
        return RedirectResponse(url="/business/signup", status_code=302)

    original_filename = (menu_file.filename or "").strip() or "menu_upload.bin"
    file_bytes = await menu_file.read()

    if not file_bytes:
        return RedirectResponse(url="/business/menu", status_code=302)

    try:
        if restaurant.menu_json_path:
            existing_menu = get_json_file(restaurant.menu_json_path)
            backup_key = f"restaurants/{restaurant.slug}/menu-backups/{uuid4()}_menu.json"
            save_json_file(backup_key, existing_menu)
    except Exception:
        pass

    try:
        menu_dataset = ingest_menu_file_to_dataset(
            file_bytes=file_bytes,
            filename=original_filename,
            business_name=restaurant.name,
            email=current_user.email,
            phone=restaurant.phone or "",
            address=restaurant.address or "",
            opening_hours=restaurant.opening_hours or "",
            currency="GBP",
            pickup_only=True,
        )
    except Exception:
        return RedirectResponse(url="/business/menu", status_code=302)

    menu_json_s3_key = restaurant.menu_json_path or f"restaurants/{restaurant.slug}/menu.json"

    try:
        save_json_file(menu_json_s3_key, menu_dataset)
        restaurant.menu_json_path = menu_json_s3_key
        db.add(restaurant)
        db.commit()
        clear_menu_cache(restaurant.slug)
    except Exception:
        db.rollback()

    return RedirectResponse(url="/business/menu", status_code=302)


# --------------------------------
# Public Pages
# --------------------------------

@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})


@router.get("/feedback", response_class=HTMLResponse)
def feedback_page(request: Request):
    return templates.TemplateResponse("feedback.html", {"request": request})


# --------------------------------
# Pricing Page
# --------------------------------

@router.get("/business/pricing", response_class=HTMLResponse)
def business_pricing_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    return templates.TemplateResponse(
        "business_pricing.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
        },
    )


# --------------------------------
# Signup Page
# --------------------------------

@router.get("/business/signup", response_class=HTMLResponse)
def business_signup_page(request: Request, plan: str = "", db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    return templates.TemplateResponse(
        "business_signup.html",
        {
            "request": request,
            "error": None,
            "plan": plan,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
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
    name = name.strip()
    business_name = business_name.strip()
    email_norm = email.strip().lower()
    phone = phone.strip()
    address = address.strip()
    opening_hours = opening_hours.strip()
    plan = plan.strip().lower() or "starter"

    existing_user = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if existing_user:
        return render_signup_error(
            request,
            plan,
            "An account with that email already exists.",
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
            email=email_norm,
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

    menu_json_s3_key = f"restaurants/{slug}/menu.json"

    try:
        save_json_file(menu_json_s3_key, menu_dataset)
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
            email=email_norm,
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
        db.refresh(user)
        db.refresh(restaurant)

        print(
            "SIGNED UP USER:",
            user.id,
            user.email,
            "RESTAURANT:",
            restaurant.id,
            restaurant.slug,
            flush=True,
        )

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
    set_session_cookie(response, user.email)
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
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    restaurant = db.query(Restaurant).filter(Restaurant.slug == slug).first()

    return templates.TemplateResponse(
        "business_onboarding_complete.html",
        {
            "request": request,
            "slug": slug,
            "restaurant": restaurant,
            "qr_download_url": get_qr_download_url(restaurant),
            "current_user": current_user,
            "dashboard_url": dashboard_url,
        },
    )


@router.get("/business/login", response_class=HTMLResponse)
def business_login_page(
    request: Request,
    next: str = "/business",
    error: str = "",
    db: Session = Depends(get_db),
):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    return templates.TemplateResponse(
        "business_login.html",
        {
            "request": request,
            "next": next,
            "error": error,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
        },
    )


@router.get("/business/qr", response_class=HTMLResponse)
def business_qr_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_platform_user(request, db)
    dashboard_url = build_dashboard_url_for_user(db, current_user)

    if not current_user:
        return RedirectResponse(url="/business/login?next=/business/qr", status_code=302)

    restaurant = get_latest_restaurant_for_user(db, current_user)
    if not restaurant:
        return RedirectResponse(url="/business/signup", status_code=302)

    return templates.TemplateResponse(
        "business_qr.html",
        {
            "request": request,
            "current_user": current_user,
            "dashboard_url": dashboard_url,
            "restaurant": restaurant,
            "qr_download_url": get_qr_download_url(restaurant),
            "public_order_url": get_public_order_url(restaurant),
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

    categories = normalize_menu_categories(data.get("categories") or [])

    return {
        "ok": True,
        "slug": slug,
        "menu_json_path": restaurant.menu_json_path,
        "category_names": [c["name"] for c in categories],
        "item_names": [str(i.get("name") or "") for i in data.get("items", [])[:20]],
        "raw": data,
    }
