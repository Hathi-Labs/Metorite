"""Load a model key into os.environ WITHOUT printing it.

The Metorite checkout has no .env. The pre-fork CommandCenter checkout on
this box does, so the spike reads the named keys from there. Values never
leave this process; only names and lengths are printed.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILES = [
    Path("C:/Users/VijayRaghavVarada/Documents/Github/Metorite/.env"),
    Path("C:/Users/VijayRaghavVarada/Documents/Github/CommandCenter/.env"),
]
WANTED = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY")


def load_keys(verbose: bool = True) -> dict[str, str]:
    found: dict[str, str] = {}
    for env_file in ENV_FILES:
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name in WANTED and value and name not in found:
                found[name] = str(env_file)
                os.environ.setdefault(name, value)
    if verbose:
        for name in WANTED:
            src = found.get(name)
            print(f"  {name}: {'present (len %d) from %s' % (len(os.environ[name]), src) if src else 'absent'}")
    return found


if __name__ == "__main__":
    load_keys()
