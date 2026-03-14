from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.stripe_service import create_checkout_session

router = APIRouter()


@router.post("/checkout")
async def checkout(request: Request):
    base_url = str(request.base_url).rstrip("/")
    url = create_checkout_session(base_url)
    return RedirectResponse(url=url, status_code=303)


@router.get("/success", response_class=HTMLResponse)
async def billing_success():
    return """
    <html>
      <body style="font-family: Arial, sans-serif; padding: 40px;">
        <h1>Payment successful</h1>
        <p>Your test subscription was created.</p>
        <p><a href="/business">Back to dashboard</a></p>
      </body>
    </html>
    """


@router.get("/cancel", response_class=HTMLResponse)
async def billing_cancel():
    return """
    <html>
      <body style="font-family: Arial, sans-serif; padding: 40px;">
        <h1>Checkout cancelled</h1>
        <p>No payment was completed.</p>
        <p><a href="/business">Back to dashboard</a></p>
      </body>
    </html>
    """