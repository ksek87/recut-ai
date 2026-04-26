from __future__ import annotations

import os

# Pricing per million tokens: (input_price, output_price)
ANTHROPIC_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "claude-haiku-4-5": (0.80, 4.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.80, 4.0),
    "claude-3-opus-20240229": (15.0, 75.0),
}

OPENAI_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.0, 60.0),
    "o1-mini": (3.0, 12.0),
    "o3-mini": (1.10, 4.40),
}


def resolve_cost(
    pricing_table: dict[str, tuple[float, float]],
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    strip_date_suffix: bool = False,
) -> float | None:
    """
    Compute token cost with env-var override support.

    Set RECUT_PRICE_INPUT and RECUT_PRICE_OUTPUT (per million tokens) to override
    the built-in pricing table — useful for discounted or non-USD billing rates.
    """
    env_input = os.environ.get("RECUT_PRICE_INPUT")
    env_output = os.environ.get("RECUT_PRICE_OUTPUT")
    if env_input is not None and env_output is not None:
        try:
            input_price = float(env_input)
            output_price = float(env_output)
            return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
        except ValueError:
            pass

    lookup = _normalize_model(model) if strip_date_suffix else model
    pricing = pricing_table.get(lookup) or pricing_table.get(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def format_cost(cost: float) -> str:
    """Format a cost value using the configured cost unit (default: USD)."""
    unit = os.environ.get("RECUT_COST_UNIT", "USD")
    if unit == "USD":
        return f"${cost:.4f}"
    return f"{cost:.4f} {unit}"


def _normalize_model(model: str) -> str:
    """Strip date suffixes like '-2024-11-20' from versioned model names."""
    parts = model.split("-")
    for i, part in enumerate(parts):
        if len(part) == 4 and part.isdigit():
            return "-".join(parts[:i])
    return model
