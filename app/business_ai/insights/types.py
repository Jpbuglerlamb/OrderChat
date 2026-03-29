# app/business_ai/insights/types.py
from __future__ import annotations

from typing import Any, Literal, TypedDict


InsightPriority = Literal["high", "medium", "low"]


class Insight(TypedDict):
    type: str
    priority: InsightPriority
    title: str
    summary: str
    action: str
    evidence: dict[str, Any]