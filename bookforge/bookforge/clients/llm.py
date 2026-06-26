"""Anthropic Claude client with structured-output + budget tracking.

`structured()` forces the model to return JSON matching a Pydantic schema by
exposing it as a single tool and requiring that tool call. This is how we get
validated data (BookBible, Chapter, claim verdicts) instead of parsing prose.
"""
from __future__ import annotations

import json
from typing import TypeVar

import anthropic
import structlog
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from bookforge.config import settings
from bookforge.cost import budget

log = structlog.get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

# Approx per-MTok USD; used for the per-book budget guardrail, not billing.
_PRICE = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set — populate .env")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _track(model: str, usage) -> None:
    pin, pout = _PRICE.get(model, _PRICE["claude-opus-4-8"])
    cost = (usage.input_tokens * pin + usage.output_tokens * pout) / 1_000_000
    budget.charge(cost)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30))
def complete(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 8000,
    temperature: float | None = None,
) -> str:
    """Plain text completion. `temperature` is omitted unless explicitly set —
    newer models (e.g. Opus 4.8) reject the deprecated parameter."""
    model = model or settings.content_model
    budget.assert_ok()
    kwargs = {} if temperature is None else {"temperature": temperature}
    resp = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    _track(model, resp.usage)
    return "".join(b.text for b in resp.content if b.type == "text")


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30))
def structured(
    prompt: str,
    schema: type[T],
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 8000,
    temperature: float | None = None,
) -> T:
    """Force the model to emit JSON validated against `schema`."""
    model = model or settings.content_model
    budget.assert_ok()
    tool = {
        "name": "emit",
        "description": f"Return a {schema.__name__} object.",
        "input_schema": schema.model_json_schema(),
    }
    kwargs = {} if temperature is None else {"temperature": temperature}
    resp = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or anthropic.NOT_GIVEN,
        tools=[tool],
        tool_choice={"type": "tool", "name": "emit"},
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    _track(model, resp.usage)
    for block in resp.content:
        if block.type == "tool_use" and block.name == "emit":
            return schema.model_validate(block.input)
    raise ValueError(f"Model did not return a valid {schema.__name__}: {resp.content!r}")


def vision_json(prompt: str, image_bytes: bytes, schema: type[T], *, media_type="image/jpeg") -> T:
    """Vision call (image QA / scoring) returning validated JSON."""
    import base64

    model = settings.vision_qa_model
    budget.assert_ok()
    tool = {
        "name": "emit",
        "description": f"Return a {schema.__name__} object.",
        "input_schema": schema.model_json_schema(),
    }
    resp = _get_client().messages.create(
        model=model,
        max_tokens=2000,
        tools=[tool],
        tool_choice={"type": "tool", "name": "emit"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type,
                    "data": base64.b64encode(image_bytes).decode(),
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    _track(model, resp.usage)
    for block in resp.content:
        if block.type == "tool_use":
            return schema.model_validate(block.input)
    raise ValueError("Vision model returned no structured output")
