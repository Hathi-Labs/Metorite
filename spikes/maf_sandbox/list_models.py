"""List a few OpenRouter models with prices (public endpoint, no key)."""

import json
import urllib.request

d = json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30))["data"]
for m in d:
    mid = m["id"]
    if any(k in mid for k in ("gpt-4.1", "gpt-5", "claude-sonnet", "claude-haiku", "gemini-2.5-flash", "qwen3-coder")):
        p = m.get("pricing", {})
        tools = "tools" in (m.get("supported_parameters") or [])
        print(f"{mid:45s} in ${float(p.get('prompt', 0)) * 1e6:6.2f}/M out ${float(p.get('completion', 0)) * 1e6:6.2f}/M {'tools' if tools else ''}")
