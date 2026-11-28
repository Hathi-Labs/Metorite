"""Probe Gemini and Groq OpenAI-compatible endpoints, plus the host of the
COPILOT_LLM_BASE_URL entry. Prints no secrets."""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from keys import load_keys
from openai import OpenAI

load_keys(verbose=False)

# Host only, for the BYOK entry. Never the key.
for line in Path("C:/Users/VijayRaghavVarada/Documents/Github/CommandCenter/.env").read_text(
    encoding="utf-8", errors="replace"
).splitlines():
    if line.startswith(("COPILOT_LLM_BASE_URL=", "VLLM_BASE_URL=", "COPILOT_MODEL=", "COPILOT_CHAT_MODEL=", "VLLM_MODEL=")):
        name, _, val = line.partition("=")
        val = val.strip().strip('"')
        shown = urlsplit(val).hostname if "://" in val else val
        print(f"{name}: {shown!r} (len {len(val)})")

PROBES = [
    ("gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/", ["gemini-2.5-flash", "gemini-2.5-pro"]),
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1", ["openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct-0905", "llama-3.3-70b-versatile"]),
]
for label, key_name, base_url, models in PROBES:
    key = os.environ.get(key_name)
    if not key:
        print(f"{label}: no key")
        continue
    client = OpenAI(api_key=key, base_url=base_url, timeout=30)
    for model in models:
        t = time.perf_counter()
        try:
            r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Say ok"}], max_tokens=200)
            print(f"{label}/{model}: OK {time.perf_counter() - t:.2f}s -> {r.choices[0].message.content!r} usage={r.usage.total_tokens}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label}/{model}: FAIL {type(exc).__name__}: {str(exc).replace(key, '***')[:200]}")
