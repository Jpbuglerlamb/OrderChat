# app/main.py
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Load .env early (safe for local + Render)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from app.db import Base, engine

# IMPORTANT: import models so SQLAlchemy registers all tables before create_all
from app import models  # noqa: F401

# Routers
from app.routes.auth_platform import router as auth_platform_router
from app.routes.web_platform import router as web_platform_router
from app.routes.cart_api import router as cart_router
from app.routes.command_router import router as command_router
from app.routes.web_customer import router as web_customer_router
from app.routes.stripe import router as stripe_router
from app.routes.stripe_webhooks import router as stripe_webhooks_router



def create_app() -> FastAPI:
    app = FastAPI(
        title="Takeaway Ordering API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # ---------- Static ----------
    PROJECT_ROOT = Path(__file__).resolve().parents[1]  # TakeawayDemo/
    FRONTEND_DIR = PROJECT_ROOT / "frontend"
    STATIC_DIR = FRONTEND_DIR / "static"
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ---------- Routers ----------
    app.include_router(auth_platform_router)
    app.include_router(web_platform_router)
    app.include_router(cart_router)
    app.include_router(command_router)
    app.include_router(web_customer_router)
    app.include_router(stripe_router, prefix="/billing")
    app.include_router(stripe_webhooks_router)

    # ---------- Health ----------
    @app.get("/health")
    def health():
        return {"ok": True}

    # ---------- DB init ----------
    @app.on_event("startup")
    def _startup() -> None:
        Base.metadata.create_all(bind=engine)

    return app


app = create_app()