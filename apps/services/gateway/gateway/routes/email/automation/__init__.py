"""automation (subpackage).

Split from a single large layer module into focused submodules; every
submodule name is flattened here so the parent package's public surface is
unchanged.
"""
from __future__ import annotations

from gateway.routes.email.automation import (
    actions,
    always_copy,  # noqa: F401 -- imported only so that its routes register
    analytics,
    assistant,
    cleanup,
    drafting,
    engine,
    followups,
    held_card,  # noqa: F401 -- imported only so that its routes register
    insights,  # noqa: F401 -- imported only so that its route registers
    learning,
    reply_card,  # noqa: F401 -- imported only so that its routes register
    replyzero,
    rule_copy,
    rules,
    runner,
    send_later,  # noqa: F401 -- imported only so that its routes register
    senders,
    voice_profile,
)  # noqa: F401

# `analytics` imports from `senders`, so it is flattened after it — the loop
# order decides which module wins a name collision. `rule_copy` imports from
# `rules`, so it comes after `rules` (EM-T8f-1).
for _mod in (assistant, drafting, engine, replyzero, followups,
             actions, learning, rules, rule_copy, runner, senders, cleanup,
             analytics, voice_profile):
    for _k, _v in vars(_mod).items():
        if not _k.startswith("__"):
            globals()[_k] = _v
del _mod, _k, _v

# `insights` (WS-17 EM-T14c) is not flattened. Its names stay in its module.
# Nor is `reply_card` (EM-CR, email_app_master_plan.md §18), nor
# `send_later` (H-375, §19), nor `held_card` (§21).
