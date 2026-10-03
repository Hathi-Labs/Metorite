"""automation (subpackage).

Split from a single large layer module into focused submodules; every
submodule name is flattened here so the parent package's public surface is
unchanged.
"""
from __future__ import annotations

from gateway.routes.email.automation import (
    actions,
    analytics,
    assistant,
    chat,
    cleanup,
    drafting,
    engine,
    followups,
    learning,
    replyzero,
    rule_copy,
    rules,
    runner,
    senders,
    voice_profile,
)  # noqa: F401

# `analytics` imports from `senders`, so it is flattened after it — the loop
# order decides which module wins a name collision. `rule_copy` imports from
# `rules`, so it comes after `rules` (EM-T8f-1).
for _mod in (assistant, drafting, engine, replyzero, chat, followups,
             actions, learning, rules, rule_copy, runner, senders, cleanup,
             analytics, voice_profile):
    for _k, _v in vars(_mod).items():
        if not _k.startswith("__"):
            globals()[_k] = _v
del _mod, _k, _v
