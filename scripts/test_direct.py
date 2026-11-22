#!/usr/bin/env python3
"""Test DeepSeek via LiteLLM SDK directly on VPS."""
import os
import sys

# API key must be set in environment — never hardcode keys in source
if not os.environ.get("DEEPSEEK_API_KEY"):
    print(
        "ERROR: DEEPSEEK_API_KEY environment variable not set.",
        file=sys.stderr,
    )
    print(
        "  Set it via: $env:DEEPSEEK_API_KEY='sk-...'  (PowerShell)",
        file=sys.stderr,
    )
    print(
        "  Or:         export DEEPSEEK_API_KEY=sk-...   (bash)",
        file=sys.stderr,
    )
    sys.exit(1)

import litellm
resp = litellm.completion(
    model="deepseek/deepseek-chat",
    messages=[{"role": "user", "content": "Say hello in exactly 3 words"}],
    max_tokens=20,
    # D58.2 (task 625): name the key per call, never through a litellm global.
    api_key=os.environ["DEEPSEEK_API_KEY"],
)
print(resp.choices[0].message.content)
