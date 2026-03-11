# app/routes/web_platform.py
from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@router.get("/customer", response_class=HTMLResponse)
def customer_page(request: Request):
    return templates.TemplateResponse("customer_home.html", {"request": request})


@router.get("/business", response_class=HTMLResponse)
def business_page(request: Request):
    return templates.TemplateResponse("business_home.html", {"request": request})

@router.get("/business/signup", response_class=HTMLResponse)
def business_signup_page(request: Request):
    return templates.TemplateResponse(
        "business_signup.html",
        {"request": request}
    )

@router.post("/business/signup")
def business_signup_submit(
    name: str = Form(...),
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    # Temporary placeholder:
    # later this will:
    # 1. create business user
    # 2. create restaurant record
    # 3. generate slug
    # 4. redirect to onboarding

    return RedirectResponse(url="/business", status_code=303)