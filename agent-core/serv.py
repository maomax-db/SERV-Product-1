"""Minimal client for the SERV Reasoning inference API (OpenServ / BRAID).

SERV is wire-compatible with the OpenAI Chat Completions format:
    POST {base_url}/chat/completions     Authorization: Bearer $OPENSERV_API_KEY
A system prompt is required on every request.

Sector 2 will grow this module into the structured trade-proposal caller
enforced against the decision schema. For now it is deliberately thin: a single
``chat()`` helper used by ``test_openserv.py`` to prove the plumbing works.
"""

from typing import Optional

import requests

from config import OPENSERV_API_KEY, OPENSERV_BASE_URL, OPENSERV_MODEL

DEFAULT_SYSTEM_PROMPT = "You are a concise assistant."


class OpenServError(RuntimeError):
    """Raised for missing config, network failure, or a non-200 HTTP response."""


def chat(
    user_prompt: str,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    model: Optional[str] = None,
    timeout: int = 60,
) -> dict:
    """Send a chat-completions request to SERV and return the raw response dict.

    Raises ``OpenServError`` on missing key, transport failure, or non-200.
    """
    if not OPENSERV_API_KEY:
        raise OpenServError(
            "OPENSERV_API_KEY is not set. Copy .env.example to .env and add your key."
        )

    payload = {
        "model": model or OPENSERV_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENSERV_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{OPENSERV_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:  # DNS / connection / timeout
        raise OpenServError(f"SERV API unreachable: {exc}") from exc

    if response.status_code != 200:
        raise OpenServError(
            f"SERV API error {response.status_code}: {response.text[:500]}"
        )

    return response.json()