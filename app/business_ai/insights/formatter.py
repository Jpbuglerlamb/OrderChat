#app/business_ai/insights/formatter.py
def format_insights(insights):
    if not insights:
        return "No insights available yet."

    lines = ["📊 Business AI Insights", ""]

    for index, insight in enumerate(insights, start=1):
        lines.append(f"{index}. {insight}")

    return "\n".join(lines)