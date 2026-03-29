# app/business_ai/actions/formatter.py
from __future__ import annotations

from app.business_ai.actions.recommendations import Recommendation


_PRIORITY_EMOJI = {
    "high": "🚀",
    "medium": "⚡",
    "low": "🧩",
}


def format_recommendations(recommendations: list[Recommendation]) -> str:
    if not recommendations:
        return "No recommendations available yet."

    lines: list[str] = ["🎯 Recommended Actions", ""]

    for index, rec in enumerate(recommendations, start=1):
        emoji = _PRIORITY_EMOJI.get(rec.get("priority", "low"), "•")
        title = rec.get("title", "Recommendation")
        summary = rec.get("summary", "").strip()
        action = rec.get("action", "").strip()
        reason = rec.get("reason", "").strip()

        lines.append(f"{index}. {emoji} {title}")

        if summary:
            lines.append(f"   {summary}")

        if action:
            lines.append(f"   Action: {action}")

        if reason:
            lines.append(f"   Why: {reason}")

        lines.append("")

    return "\n".join(lines).strip()