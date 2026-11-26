"""``--mirror-prod``: the chat flags of production, in the eval process only (task 2874, part 3).

``prod_flags.json`` holds the values that the coordinator read on the production box. With
``--mirror-prod`` each chat of the pack arms gets them, so a live run sees the packs, the held
cards and the autoload that a member sees. Without the switch, a run is as before, so the old
results stay comparable. The runner never writes a flag on a box.

A setting name is the env name in lower case. :func:`settings_for` checks that each name is a
field of the real settings class, so a misspelt key fails the run. ``setattr(..., raising=False)``
in ``run.run_chat`` would accept it in silence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PATH = Path(__file__).resolve().parent / "prod_flags.json"


def load() -> dict[str, Any]:
    return json.loads(PATH.read_text(encoding="utf-8"))


def header() -> dict[str, Any]:
    """What the results header records: the read date, the source and the values."""
    data = load()
    return {"read": data["read"], "source": data["source"], "flags": dict(data["flags"])}


def settings_for(org: str) -> dict[str, Any]:
    """The settings to set, by field name. An org list that holds ``*`` is *org*."""
    from acb_common import get_settings

    fields = type(get_settings()).model_fields
    data = load()
    out: dict[str, Any] = {}
    for env, raw in data["flags"].items():
        name = env.lower()
        if name not in fields:
            raise KeyError(f"prod_flags.json: {env} is no field of the settings ({name})")
        value: Any = raw
        if fields[name].annotation is bool:
            value = str(raw).strip().lower() in ("1", "true", "yes", "on")
        elif env in data["org_lists"] and str(raw).strip() == "*":
            value = org
        out[name] = value
    return out
