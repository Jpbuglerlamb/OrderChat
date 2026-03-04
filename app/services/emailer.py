# app/emailer.py
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error


POSTMARK_URL = "https://api.postmarkapp.com/email"


def send_order_email(
    to_email: str,
    subject: str,
    body: str,
    *,
    reply_to: str | None = None,
    message_stream: str | None = None,
) -> None:
    """
    Sends a plain-text email via Postmark Email API.

    Required env vars:
      POSTMARK_SERVER_TOKEN
      POSTMARK_FROM_EMAIL
    Optional:
      POSTMARK_MESSAGE_STREAM (fallback if message_stream not provided)
    """
    token = (os.environ.get("POSTMARK_SERVER_TOKEN") or "").strip()
    from_email = (os.environ.get("POSTMARK_FROM_EMAIL") or "").strip()
    default_stream = (os.environ.get("POSTMARK_MESSAGE_STREAM") or "").strip()

    if not token or not from_email:
        raise RuntimeError("Postmark not configured. Set POSTMARK_SERVER_TOKEN and POSTMARK_FROM_EMAIL.")

    payload: dict[str, str] = {
        "From": from_email,
        "To": (to_email or "").strip(),
        "Subject": subject or "",
        "TextBody": body or "",
    }

    if reply_to:
        payload["ReplyTo"] = reply_to.strip()

    # Postmark: MessageStream is optional; if you don’t use streams, you can remove this
    stream = (message_stream or default_stream).strip()
    if stream:
        payload["MessageStream"] = stream

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url=POSTMARK_URL,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": token,
            "User-Agent": "JP-Ordering/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            # Keep for debugging if needed
            _ = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        details = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Postmark HTTPError {e.code}: {details}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Postmark URLError: {e}") from e