"""Thin wrapper around the OpenAI client used by the Intake Agent.

Model IDs are never hardcoded here; they come from environment variables so
they can be swapped without touching code (see the spec's model-comparison
requirements).
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def _get_model(model_env_var: str) -> str:
    model = os.getenv(model_env_var)
    if not model:
        raise RuntimeError(
            f"{model_env_var}가 설정되어 있지 않습니다. .env 파일을 확인하세요."
        )
    return model


def call_json(prompt: str, model_env_var: str) -> dict:
    """Call the chat completion API and parse a JSON object from the response."""
    model = _get_model(model_env_var)
    client = _get_client()

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON 응답이 객체가 아닙니다.")
    return parsed


def call_text(prompt: str, model_env_var: str) -> str:
    """Call the chat completion API and return plain text."""
    model = _get_model(model_env_var)
    client = _get_client()

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()
