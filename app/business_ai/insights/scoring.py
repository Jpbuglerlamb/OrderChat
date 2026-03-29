from __future__ import annotations

from typing import Any


def score_insight(insight: dict[str, Any]) -> float:
    priority_weights = {
        "high": 1.0,
        "medium": 0.7,
        "low": 0.4,
    }
    base = priority_weights.get(insight.get("priority", "low"), 0.4)

    evidence = insight.get("evidence", {})
    confidence = float(evidence.get("confidence", 0.7))

    return round(base * confidence, 3)