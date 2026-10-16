"""
ai/llm_service.py

Wraps the OpenRouter API call (OpenAI-compatible /chat/completions). Enforces
JSON-only structured responses and never lets a malformed/unparseable
LLM response fall through to execution.
"""

import os
import json
import re

from ai.prompts import SYSTEM_PROMPT
from ai.openrouter_client import chat_json, OpenRouterError

ALLOWED_ACTIONS = {"analyze", "execute", "clarify"}
ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}

REQUIRED_FIELDS = {
    "intent", "action", "command", "explanation",
    "recommendation", "risk_level", "requires_confirmation",
}


class LLMResponseError(Exception):
    pass


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _validate_schema(data: dict) -> dict:
    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise LLMResponseError(f"LLM response missing required fields: {missing}")
    if data["action"] not in ALLOWED_ACTIONS:
        raise LLMResponseError(f"Invalid action: {data['action']}")
    if data["risk_level"] not in ALLOWED_RISK_LEVELS:
        raise LLMResponseError(f"Invalid risk_level: {data['risk_level']}")
    data.setdefault("clarifying_question", None)
    return data


def call_llm(prompt: str) -> dict:
    """
    Calls OpenRouter with the system prompt + user prompt, parses and
    validates the structured JSON response. Raises LLMResponseError on any
    malformed output rather than silently passing bad data downstream.
    """
    try:
        raw_text = chat_json(SYSTEM_PROMPT, prompt, temperature=0.2, expect_json=True)
    except OpenRouterError as e:
        raise LLMResponseError(str(e))

    cleaned = _strip_code_fences(raw_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Some free models wrap JSON in extra prose — pull out the outermost object.
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        try:
            data = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            raise LLMResponseError(f"LLM did not return valid JSON.\nRaw output: {raw_text[:500]}")

    if not isinstance(data, dict):
        raise LLMResponseError("LLM returned JSON that is not an object.")

    return _validate_schema(data)


if __name__ == "__main__":
    # Smoke test — requires OPENROUTER_API_KEY to actually succeed.
    from ai.prompts import build_analysis_prompt
    dummy_context = {"disk": {"used_percent": 92, "mountpoint": "/"}}
    try:
        result = call_llm(build_analysis_prompt("Why is my disk almost full?", dummy_context))
        print(json.dumps(result, indent=2))
    except LLMResponseError as e:
        print(f"Expected failure without API key configured: {e}")
