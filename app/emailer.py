# app/emailer.py
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error


def send_order_email(to_email: str, subject: str, body: str) -> None:
    """
    Sends a plain-text email via Postmark Email API.

    Required env vars:
      POSTMARK_SERVER_TOKEN
      POSTMARK_FROM_EMAIL

    Postmark endpoint: POST https://api.postmarkapp.com/email
    Auth header: X-Postmark-Server-Token
    """
    token = (os.environ.get("POSTMARK_SERVER_TOKEN") or "").strip()
    from_email = (os.environ.get("POSTMARK_FROM_EMAIL") or "").strip()

    if not token or not from_email:
        raise RuntimeError("Postmark not configured. Set POSTMARK_SERVER_TOKEN and POSTMARK_FROM_EMAIL.")

    payload = {
        "From": from_email,
        "To": to_email,
        "Subject": subject,
        "TextBody": body,
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url="https://api.postmarkapp.com/email",
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": token,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            # Postmark returns JSON; not strictly needed, but useful for debugging
            _ = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        details = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Postmark HTTPError {e.code}: {details}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Postmark URLError: {e}") from e