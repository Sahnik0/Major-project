import re
from typing import Any, Dict, List, Set

import httpx

from apps.api.app.config import settings


def extract_numbers_from_text(text: str) -> List[float]:
    """Extract numeric values from narrative text (ignoring percentage signs and commas)."""
    # Match integers and floats
    raw_nums = re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", text)
    clean_nums: List[float] = []
    for s in raw_nums:
        val = s.replace(",", "")
        try:
            clean_nums.append(float(val))
        except ValueError:
            continue
    return clean_nums


def _collect_fact_numbers(obj: Any) -> Set[float]:
    """Recursively collect all numeric values present in the facts dictionary, including numbers inside strings/dates."""
    numbers: Set[float] = set()
    if isinstance(obj, bool):
        return numbers
    if isinstance(obj, (int, float)):
        if not (obj != obj):  # skip NaN
            numbers.add(float(obj))
            numbers.add(float(round(obj)))
            numbers.add(float(round(obj, 1)))
            numbers.add(float(round(obj, 2)))
    elif isinstance(obj, str):
        for n in extract_numbers_from_text(obj):
            numbers.add(n)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                for n in extract_numbers_from_text(k):
                    numbers.add(n)
            numbers.update(_collect_fact_numbers(v))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            numbers.update(_collect_fact_numbers(item))
    return numbers


def validate_narrative_traceability(
    text: str,
    facts: Dict[str, Any],
    tolerance: float = 1.5,
) -> bool:
    """
    Principle 2: Every number in an AI explanation must be traceable.
    Extract every number from the generated text and verify it exists in the facts JSON.
    Allows structural list numbers (e.g. 1..20, common CI levels) and rounding tolerance.
    """
    extracted = extract_numbers_from_text(text)
    if not extracted:
        return True

    allowed_numbers = _collect_fact_numbers(facts)
    structural = {float(i) for i in range(0, 21)} | {80.0, 90.0, 95.0, 100.0}

    for num in extracted:
        if num in structural:
            continue
        matched = False
        for allowed in allowed_numbers:
            if abs(num - allowed) <= tolerance or (abs(allowed) > 0 and abs(num - allowed) / abs(allowed) < 0.03):
                matched = True
                break
        if not matched:
            return False
    return True


def deterministic_template_narrative(facts: Dict[str, Any]) -> str:
    """Deterministic fallback narrative constructed solely from verified facts."""
    total_units = facts.get("total_forecast_units", 0)
    total_prods = facts.get("total_products", 0)
    risk_count = facts.get("high_risk_count", 0)
    top_reorders = facts.get("top_reorders", [])
    error_pct = facts.get("average_forecast_error_pct")

    lines = [
        f"Your sales demand forecast for {total_prods} products totals approximately {total_units:,} units over the upcoming period."
    ]

    if error_pct is not None:
        lines.append(f"Across backtested models, forecasts demonstrated an average historical deviation of about {error_pct}%.")

    if risk_count > 0:
        lines.append(f"{risk_count} products have been identified at potential stockout risk based on your lead times.")

    if top_reorders:
        reorder_items = ", ".join(f"{r['product']} ({r['recommended_qty']} units)" for r in top_reorders[:3])
        lines.append(f"Top immediate reorder recommendations include: {reorder_items}.")
    else:
        lines.append("Current inventory levels across analyzed products are currently healthy.")

    inactive = facts.get("inactive_products_count", 0)
    if inactive > 0:
        lines.append(f"Note: {inactive} products have recorded no recent sales and are flagged as inactive.")

    return " ".join(lines)


async def generate_business_narrative(facts: Dict[str, Any]) -> str:
    """
    Calls Gemma via Ollama with a strict prompt and validates numbers against facts.
    If validation fails or Ollama is unavailable, falls back to deterministic template.
    """
    if not facts:
        return "No forecast facts available."

    system_prompt = (
        "You are an executive retail advisor explaining demand forecasts to a shop owner.\n"
        "RULES:\n"
        "1. Speak in plain, reassuring English. Avoid jargon like 'MASE', 'quantiles', or 'heteroscedasticity'.\n"
        "2. State 3 to 5 concise sentences highlighting total expected sales and immediate reorder actions.\n"
        "3. NEVER invent numbers. Use ONLY numbers provided in the FACTS JSON below. If you mention a number, it MUST be exact.\n"
    )

    user_prompt = f"FACTS:\n{facts}\n\nWrite a 3-4 sentence plain-English summary for the store owner:"

    for _attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{settings.OLLAMA_URL}/api/generate",
                    json={
                        "model": settings.OLLAMA_MODEL,
                        "prompt": f"{system_prompt}\n\n{user_prompt}",
                        "stream": False,
                    },
                )
                if response.status_code == 200:
                    text = response.json().get("response", "").strip()
                    # Validate
                    if validate_narrative_traceability(text, facts):
                        return text
        except Exception:
            break

    # Fallback to deterministic template
    return deterministic_template_narrative(facts)

