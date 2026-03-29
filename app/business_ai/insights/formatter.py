# app/business_ai/insights/formatter.py
from __future__ import annotations

from app.business_ai.insights.types import Insight


_PRIORITY_EMOJI = {
    "high": "🔥",
    "medium": "⚡",
    "low": "🧩",
}


def format_insights(insights: list[Insight]) -> str:
    if not insights:
        return "No insights available yet."

    lines: list[str] = ["📊 Business AI Insights", ""]

    for index, insight in enumerate(insights, start=1):
        emoji = _PRIORITY_EMOJI.get(insight.get("priority", "low"), "•")
        title = insight.get("title", "Insight")
        summary = insight.get("summary", "").strip()
        action = insight.get("action", "").strip()

        lines.append(f"{index}. {emoji} {title}")

        if summary:
            lines.append(f"   {summary}")

        if action:
            lines.append(f"   Action: {action}")

        lines.append("")

    return "\n".join(lines).strip()