"""
ai/openrouter_client.py

Single shared OpenRouter client used by BOTH the assistant (ai/llm_service.py)
and the automatic evaluator (evaluation/auto_evaluator.py).

OpenRouter exposes an OpenAI-compatible /chat/completions endpoint and lists
many models with a ":free" suffix. Free models can be rate limited or
temporarily unavailable, so this client:
  - tries the primary model (OPENROUTER_MODEL), then optional fallbacks
    (OPENROUTER_FALLBACK_MODELS, comma separated)
  - retries once without `response_format` if a model rejects JSON mode
  - raises OpenRouterError with a clear message if everything fails
"""

import os
import re
import json
import time

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "openrouter/free"
# The old ":free" fallbacks (gemma-3-27b, mistral-small-3.2) now return
# HTTP 404 "unavailable for free", so there are no default fallbacks.
DEFAULT_FALLBACKS = []

# Free routers (e.g. "openrouter/free") pick a DIFFERENT underlying model on
# every call, so one call may return clean JSON and the next may return prose
# or a safety-classifier verdict ("User Safety: safe"). When the caller needs
# JSON we therefore validate the reply and retry a few times before giving up.
JSON_RETRIES_PER_MODEL = 3

REQUEST_TIMEOUT = 60


class OpenRouterError(Exception):
    pass


def _get_models() -> list:
    primary = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    fallback_env = os.environ.get("OPENROUTER_FALLBACK_MODELS")
    if fallback_env is None:
        fallbacks = DEFAULT_FALLBACKS
    else:
        fallbacks = [m.strip() for m in fallback_env.split(",") if m.strip()]
    models = [primary] + [m for m in fallbacks if m != primary]
    return models


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Optional attribution headers recommended by OpenRouter.
        "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "http://127.0.0.1:5000"),
        "X-Title": os.environ.get("OPENROUTER_APP_NAME", "AI Linux Assistant"),
    }


def _post(api_key: str, payload: dict):
    return requests.post(
        OPENROUTER_URL,
        headers=_headers(api_key),
        data=json.dumps(payload),
        timeout=REQUEST_TIMEOUT,
    )


def extract_json_object(text: str):
    """
    Returns the outermost JSON object found in `text` as a clean JSON string,
    or None if there isn't a parseable one. Handles ```json fences and models
    that wrap the JSON in explanatory prose.
    """
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    candidates = [cleaned]
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return cand
    return None


def chat_json(system_prompt: str, user_prompt: str, temperature: float = 0.2,
              expect_json: bool = False) -> str:
    """
    Sends a system + user prompt and returns the raw assistant text.

    expect_json=False (default): caller parses/validates, same as before.
    expect_json=True: the reply must contain a parseable JSON object. If a
    model answers with prose or a safety-classifier verdict instead, that
    model is retried (free routers rotate models between calls) and then the
    next model is tried; only the clean JSON string is returned.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not set. Add it to your .env file — see .env.example."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    errors = []
    attempts_per_model = 1 + (JSON_RETRIES_PER_MODEL if expect_json else 0)

    for model in _get_models():
        for attempt in range(attempts_per_model):
            retry_same_model = False

            # First try with JSON mode; some free models reject it, so retry without.
            for use_json_mode in (True, False):
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if use_json_mode:
                    payload["response_format"] = {"type": "json_object"}

                try:
                    resp = _post(api_key, payload)
                except requests.RequestException as e:
                    errors.append(f"{model}: network error: {e}")
                    break  # network problem — move to next model

                if resp.status_code == 200:
                    try:
                        body = resp.json()
                        content = body["choices"][0]["message"]["content"]
                    except (ValueError, KeyError, IndexError, TypeError):
                        errors.append(f"{model}: unexpected response shape")
                        break
                    if content and content.strip():
                        if not expect_json:
                            return content
                        extracted = extract_json_object(content)
                        if extracted is not None:
                            return extracted
                        errors.append(
                            f"{model}: reply was not a JSON object "
                            f"(got: {content.strip()[:60]!r})"
                        )
                        retry_same_model = True
                        break
                    errors.append(f"{model}: empty response")
                    retry_same_model = expect_json
                    break

                detail = resp.text[:300].replace("\n", " ")

                if resp.status_code == 401:
                    raise OpenRouterError(
                        "OpenRouter rejected the API key (401). Check OPENROUTER_API_KEY."
                    )

                if resp.status_code == 400 and use_json_mode:
                    # Likely JSON mode unsupported for this model — retry without it.
                    continue

                if resp.status_code == 429:
                    errors.append(f"{model}: rate limited (429)")
                    time.sleep(1)
                    break

                errors.append(f"{model}: HTTP {resp.status_code}: {detail}")
                break

            if not retry_same_model:
                break  # move on to the next model

    raise OpenRouterError(
        "OpenRouter call failed for all models. Details: " + " | ".join(errors)
    )
