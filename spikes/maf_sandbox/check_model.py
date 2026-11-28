"""Probe which provider keys work, with a 1-token call each. Prints no secrets."""

from __future__ import annotations

import os
import time

from keys import load_keys
from openai import OpenAI

load_keys()

PROBES = [
    ("openai", "OPENAI_API_KEY", None, ["gpt-4.1-mini", "gpt-4.1", "gpt-5-mini", "gpt-4o-mini"]),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", ["openai/gpt-4.1-mini"]),
    ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", ["deepseek-chat"]),
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
            r = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "Say ok"}], max_completion_tokens=16
            )
            print(f"{label}/{model}: OK {time.perf_counter() - t:.2f}s -> {r.choices[0].message.content!r} usage={r.usage.total_tokens}")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).replace(key, "***")[:160]
            print(f"{label}/{model}: FAIL {type(exc).__name__}: {msg}")
