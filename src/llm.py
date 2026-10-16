"""Optional LLM backend (Groq/OpenAI/Ollama) via OpenAI-compatible client."""
from __future__ import annotations

from .config import (LLM_PROVIDER, GROQ_API_KEY, OPENAI_API_KEY, OLLAMA_BASE_URL,
                     GROQ_MODEL, OPENAI_MODEL, OLLAMA_MODEL)


def _resolve():
    p = LLM_PROVIDER
    if p == "auto":
        if GROQ_API_KEY:
            p = "groq"
        elif OPENAI_API_KEY:
            p = "openai"
        else:
            p = "none"
    if p == "groq" and not GROQ_API_KEY:
        p = "none"
    if p == "openai" and not OPENAI_API_KEY:
        p = "none"
    return p


def backend_name() -> str:
    return _resolve()


def is_available() -> bool:
    return _resolve() != "none"


def chat(messages, temperature=0.2, max_tokens=600):
    p = _resolve()
    if p == "none":
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    if p == "groq":
        client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
        model = GROQ_MODEL
    elif p == "openai":
        client = OpenAI(api_key=OPENAI_API_KEY)
        model = OPENAI_MODEL
    else:
        client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL)
        model = OLLAMA_MODEL
    try:
        resp = client.chat.completions.create(model=model, messages=messages,
                                             temperature=temperature, max_tokens=max_tokens)
        return resp.choices[0].message.content
    except Exception as e:
        return f"[LLM error: {e}]"
