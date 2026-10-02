"""Automation · uncategorized-inbox sweep.

The Email Cleaner can only clean what it can see, and it sees mail through the
rule engine's per-message labels (``email_messages.categories``). Inbox mail the
rules never reached is therefore invisible to every category chip and filter tab
— it just sits there as an uncategorized sender with no disposition.

There are only two honest ways to close that gap:

1. **Project** what has already been decided onto the mail that looks the same.
   That is this module. It is deterministic, free and instant — no LLM, no new
   opinion. Four sources of evidence, strongest first:

   * a **learned pattern** (``email_rule_patterns``) already pins this sender or
     subject to a rule, and that rule labels with a cleanup category. These are
     the patterns the user taught via Fix / label edits, plus the ones auto-
     learned from confident AI matches — the highest-confidence signal there is.
   * **sender consensus**: the rules already labelled enough of *this sender's*
     mail, consistently, with one cleanup category.
   * **domain consensus**: same, one level up, for a sender too new to have its
     own history (``billing@stripe.com`` inheriting from ``stripe.com``).
   * **bulk shape**: properties of the message itself that need no history at
     all — a ``List-Unsubscribe`` header, or an unattended local-part like
     ``noreply@`` / ``alerts@``. This is the only step that can reach a sender
     seen for the first time, which is most of a real backlog: measured on a
     live mailbox, the first three steps could reach 265 of 4,391 uncategorized
     messages, because they all require the sender to have been labelled before.

2. **Classify** it, which is what the rule engine already does. This module never
   does that — when there is no evidence it reports the message as needing a
   rules run and stops. That is the whole point: a second classifier here would
   be exactly the parallel-categorization drift this codebase keeps paying for.

Everything applied goes through ``runner.apply_label`` (the single label writer)
and is logged to ``email_executed_rules`` so it shows up in History and can be
audited like any other automated action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from gateway.routes.email.automation.engine import (
    _load_rule_patterns,
    _pattern_hit,
)
from gateway.routes.email.automation.identity import resolve_org_domains
from gateway.routes.email.automation.jobs import JobTracker
from gateway.routes.email.automation.senders import (
    _KNOWN_LABELS_LOWER,
    _NOT_DISPOSED,
    canonical_cleanup_category,
)
from gateway.routes.email.core import (
    _assert_account_owner,
    _tenant_session,
    _instantiate_provider,
    _log,
    _persist_rotated_creds,
    _provider_for_account_any,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

# Consensus bars. Deliberately conservative: a wrong bulk label is worse than an
# uncategorized sender, because the cleaner offers destructive actions on top of
# it. "dominance" = share of the sender's/domain's labelled mail carrying the
# winning category.
_SENDER_MIN_LABELLED = 2
_SENDER_MIN_DOMINANCE = 0.8
_DOMAIN_MIN_LABELLED = 4
_DOMAIN_MIN_DOMINANCE = 0.9

# Free-mail and other shared domains where "same domain" implies nothing about
# the sender — consensus there would tar every personal contact with whatever
# category one newsletter from the same host happened to get.
_SHARED_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com",
    "proton.me", "protonmail.com", "gmx.com", "mail.com", "zoho.com",
    "qq.com", "163.com", "126.com", "yandex.ru", "msn.com",
}

# The sweep pages through the mailbox until it runs out of uncategorized mail.
# _MAX_SWEEP is a runaway backstop, not a product limit: a cap low enough to
# stop a real mailbox short would leave the user believing the cleaner finished
# when it had merely given up.
_MAX_SWEEP = 200_000
_SWEEP_PAGE = 500

# A preview must return inside one HTTP request, so it decides a sample of the
# most recent mail and says so (`sampled`). The live run is unbounded.
_PREVIEW_MAX = 2000

# In-memory progress, same contract and lifetime as runner._PAST_JOBS — now the
# SAME token-guarded JobTracker (automation/jobs.py). The gateway runs a single
# worker, so the request handler and the background task share it; a process
# restart kills the job and the tracker together. The token guard is what stops
# an overlapping sweep+backfill (or a double-clicked sweep) from clobbering each
# other's progress row — the bug this table had before and the runner did not.
_SWEEP_JOBS = JobTracker()


# ── Bulk shape ───────────────────────────────────────────────────────────────
# Signals carried by the message itself, so they work on a sender seen for the
# very first time — which the consensus steps above cannot do. Three of these
# already existed in engine.py, but only as NEGATIVE gates: they could say "this
# isn't a Reply" and were never allowed to say "this is a Notification".
#
# Local-part → category, evaluated in THIS order. Order decides mixed locals:
# ``news-noreply@`` reads as a newsletter (Newsletter is checked before
# Notification), which is what it is.
_SHAPE_CATEGORY: tuple[tuple[str, frozenset[str]], ...] = (
    ("Receipt", frozenset({
        "receipt", "receipts", "invoice", "invoices", "billing", "payment",
        "payments", "order", "orders", "statement", "statements",
    })),
    ("Newsletter", frozenset({
        "newsletter", "newsletters", "news", "digest", "bulletin",
        "update", "updates",
    })),
    ("Marketing", frozenset({
        "marketing", "promo", "promos", "promotions", "offer", "offers",
        "deals", "campaign", "campaigns",
    })),
    ("Notification", frozenset({
        "noreply", "donotreply", "notification", "notifications", "notify",
        "notif", "alert", "alerts", "mailer", "mailerdaemon", "bounce",
        "bounces", "automated", "autoreply", "system", "postmaster", "robot",
    })),
)

# Deliberately absent from every set above. A human very often reads and answers
# these, and a wrong bulk label here is the expensive kind — the Cleaner offers
# archive and unsubscribe on top of it. Observed live: ``support@fracktal.in``
# is the user's own helpdesk with 231 messages, answered by people.
_AMBIGUOUS_LOCALS = frozenset({
    "info", "support", "contact", "hello", "hi", "team", "sales", "help",
    "admin", "office", "enquiry", "enquiries", "inquiry", "service",
    "services", "care", "desk", "mail", "email",
})


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in (email or "") else ""


def _local_tokens(sender: str) -> set[str]:
    """Letter-only forms of the address local part, for prefix matching.

    Returns the whole local part with separators removed PLUS each separated
    token, so ``no-reply`` matches as ``noreply`` and ``news-noreply`` offers
    both ``news`` and ``noreply``. Digits and punctuation are stripped, which is
    what makes ``noreply2@`` and ``alerts_01@`` behave like their bare forms.
    """
    local = (sender or "").split("@", 1)[0].lower()
    out = {"".join(ch for ch in local if ch.isalpha())}
    for tok in local.replace("+", ".").replace("_", ".").replace("-", ".").split("."):
        out.add("".join(ch for ch in tok if ch.isalpha()))
    out.discard("")
    return out


# Tokens long and specific enough to be matched as a SUBSTRING of the local
# part, not just as a whole token. ``customernotification@icici.bank.in`` and
# ``hdfcbanksmartstatement@hdfcbank.net`` are unmistakably automated, and no
# amount of separator-splitting reaches them because they have no separators.
#
# The bar is length plus specificity, because substring matching is how a
# classifier starts labelling people: "news" appears inside the surname Newsom,
# "order" inside recorder, "promo" inside promontory. Every entry here is ≥8
# characters and has no common English or surname superstring.
_SHAPE_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("notification", "Notification"),
    ("notifications", "Notification"),
    ("donotreply", "Notification"),
    ("autoreply", "Notification"),
    # "noreply" is NOT here: at 7 characters it sits under the bar, and bending
    # the bar for one entry is how a rule like this stops meaning anything. It
    # is still matched as a whole token and through separator-splitting, so
    # noreply@ / no-reply@ / no_reply@ all resolve — only a run-together form
    # like ordersnoreply@ is given up, which is rare and reachable by a rule.
    ("newsletter", "Newsletter"),
    ("statement", "Receipt"),
    ("statements", "Receipt"),
    ("promotions", "Marketing"),
)


def _shape_category(sender: str, has_unsubscribe: bool) -> tuple[str, str] | None:
    """(category, reason) from the message's own shape, or None.

    A ``List-Unsubscribe`` header outranks the local part, and is checked even
    for an ambiguous local: RFC 8058 mail is bulk by definition, and it is how
    ``info@somefirm.com`` — a name that could be anyone — is correctly read as a
    newsletter blast. Transactional mail does not carry the header.

    Newsletter is the landing category for a bare unsubscribe link rather than
    Marketing: both are plausible, one is an accusation. If it is wrong the user
    hits Fix, which writes a learned pattern, and step 1 of ``_decide`` then
    outranks this forever after.
    """
    if has_unsubscribe:
        return "Newsletter", "carries a List-Unsubscribe header"
    tokens = _local_tokens(sender)
    if not tokens or tokens & _AMBIGUOUS_LOCALS:
        return None
    for category, prefixes in _SHAPE_CATEGORY:
        hit = tokens & prefixes
        if hit:
            return category, f"unattended sender ({sorted(hit)[0]}@)"
    # Run-together locals with no separator to split on. Longest first, so
    # "notifications" is reported rather than the "notification" inside it.
    flat = max(tokens, key=len)
    for needle, category in sorted(_SHAPE_SUBSTRINGS, key=lambda kv: -len(kv[0])):
        if needle in flat:
            return category, f"unattended sender ('{needle}' in address)"
    return None


def _consensus(
    counts: dict[str, int], min_labelled: int, min_dominance: float,
) -> str | None:
    """Winning cleanup category for a bag of label counts, or None.

    Requires both enough labelled mail and a clear enough winner — a sender split
    50/50 between Newsletter and Receipt teaches us nothing and must stay
    uncategorized rather than get a coin-flip.
    """
    total = sum(counts.values())
    if total < min_labelled:
        return None
    top, n = max(counts.items(), key=lambda kv: kv[1])
    return top if n / total >= min_dominance else None


async def _rule_label_by_id(db: Any, account_id: str) -> dict[str, str]:
    """``{rule_id: cleanup category}`` for rules whose LABEL action is one.

    A learned pattern points at a rule, not a category; this is how we get from
    one to the other. Rules that label with something outside the cleanup
    vocabulary (Reply / Awaiting Reply / a custom tag) are dropped — the sweep
    only fills in cleanup categories, and conversation state is Reply Zero's job.
    """
    rows = (await db.execute(text(
        """SELECT r.id::text AS rid, a.label
             FROM email_rules r
             JOIN email_actions a ON a.rule_id = r.id
            WHERE r.account_id = :aid AND r.enabled = true
              AND a.type = 'LABEL' AND COALESCE(a.label, '') <> ''
              AND COALESCE(a.label_ai, false) = false"""
    ), {"aid": account_id})).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        cat = canonical_cleanup_category(r.label)
        if cat:
            out[r.rid] = cat
    return out


async def _label_tallies(
    db: Any, account_id: str,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Per-sender and per-domain cleanup-label counts across the whole account.

    Deliberately NOT restricted to the inbox: a sender whose mail the user has
    already archived is exactly the sender whose history should teach us what
    their next message is.
    """
    rows = (await db.execute(text(
        """SELECT LOWER(em.from_address->>'email') AS email,
                  LOWER(TRIM(cat)) AS label,
                  COUNT(DISTINCT em.id) AS n
             FROM email_messages em
             CROSS JOIN LATERAL unnest(em.categories) AS cat
            WHERE em.account_id = :aid
              AND LOWER(TRIM(cat)) = ANY(:labels)
              AND COALESCE(em.from_address->>'email','') <> ''
            GROUP BY 1, 2"""
    ), {"aid": account_id, "labels": _KNOWN_LABELS_LOWER})).fetchall()
    by_sender: dict[str, dict[str, int]] = {}
    by_domain: dict[str, dict[str, int]] = {}
    for r in rows:
        cat = canonical_cleanup_category(r.label)
        if not cat:
            continue  # a conversation label — not cleanup evidence
        by_sender.setdefault(r.email, {})[cat] = int(r.n or 0)
        dom = _domain_of(r.email)
        if dom and dom not in _SHARED_DOMAINS:
            d = by_domain.setdefault(dom, {})
            d[cat] = d.get(cat, 0) + int(r.n or 0)
    return by_sender, by_domain


async def _internal_domains(db: Any, account_id: str) -> frozenset[str]:
    """The account's own domain plus any configured org domains.

    Same list ``sender_scope`` treats as internal, so "is this person a
    colleague?" has one answer across the automation package.
    """
    doms: set[str] = set()
    try:
        row = (await db.execute(text(
            "SELECT email_address FROM email_accounts WHERE id = :aid"
        ), {"aid": account_id})).fetchone()
        if row and getattr(row, "email_address", None):
            d = _domain_of(row.email_address)
            if d:
                doms.add(d)
        doms |= set(await resolve_org_domains(db, account_id))
    except Exception as exc:  # noqa: BLE001 — degrade to no internal domains
        _log.warning("email.internal_domains_failed", account_id=account_id,
                     error=str(exc)[:160])
    return frozenset(doms)


# The ONE definition of "uncategorized cleanup material". Binds :aid, :labels
# and :internal. Both the sweep (what gets worked on) and the overview badge
# (what the user is told is outstanding) build on this, because a badge counting
# mail the sweep will never touch is a to-do list that can never reach zero.
#
# "Uncategorized" means no cleanup category *and* no conversation label — a
# thread already marked Reply/Awaiting is classified, it simply isn't cleanup
# material, and re-sweeping it every run would be noise.
#
# OUTBOUND MAIL IS EXCLUDED. Cleanup categories describe inbound bulk mail — a
# message you wrote is never a Newsletter or a Cold Email. Widening the sweep
# from the inbox to the whole mailbox silently pulled the Sent folder into
# scope, where a domain consensus on the user's own company domain would have
# stamped a category across everything they ever sent.
#
# YOUR OWN ORGANISATION'S MAIL IS EXCLUDED, which is the bulk of a work mailbox:
# 2,674 of 4,391 uncategorized messages on the live account were colleagues.
# That mail is not miscategorized — its correct cleanup category is *none*, and
# no classifier will ever make it a newsletter. Counting it as outstanding work
# made the Cleaner's backlog permanently unclearable and had the sweep re-read
# thousands of colleague emails every five minutes to conclude nothing.
#
# The carve-out is deliberate and narrow: internal mail carrying a
# List-Unsubscribe header stays in scope, because an all-staff campaign blasted
# through an ESP is exactly the internal mail worth cleaning. An internal
# ``noreply@`` with no such header is NOT reachable here — it is left to the
# rules run, which has no scope limit. That is the one thing this trades away.
_CLEANUP_SCOPE = f"""
    em.account_id = :aid AND {_NOT_DISPOSED}
    AND LOWER(COALESCE(em.folder,'')) <> 'sent'
    AND COALESCE(em.from_address->>'email','') <> ''
    -- Belt and braces: self-addressed mail can sit outside Sent.
    AND LOWER(em.from_address->>'email') NOT IN (
          SELECT LOWER(email_address) FROM email_accounts WHERE id = :aid)
    AND (split_part(LOWER(em.from_address->>'email'), '@', 2) <> ALL(:internal)
         OR em.unsubscribe_link IS NOT NULL)
    AND NOT EXISTS (
          SELECT 1 FROM unnest(em.categories) AS c
           WHERE LOWER(TRIM(c)) = ANY(:labels))
    -- A message inside a live CONVERSATION is not the cleaner's to label: the
    -- thread's status IS its classification (#110), and most messages of a
    -- statused thread legitimately carry no chip (the status label sits on the
    -- latest inbound message only). Without this the sweep saw those bare
    -- messages as "uncategorized" and projected the sender's category back on
    -- — observed live 2026-07-22: a repair stripped stale Receipt/Marketing
    -- chips from conversation threads and the very next sweep cycle re-applied
    -- 36 of them. FYI rows are NOT conversations (#111), so FYI-statused bulk
    -- threads stay sweepable.
    AND NOT EXISTS (
          SELECT 1 FROM email_thread_status ts
           WHERE ts.account_id = em.account_id
             AND ts.thread_id = em.thread_id
             AND ts.status IN ('NEEDS_REPLY', 'AWAITING', 'DONE'))
"""


def _cleanup_scope_params(
    account_id: str, internal_domains: frozenset[str],
) -> dict[str, Any]:
    """Binds for :data:`_CLEANUP_SCOPE`. One helper so a caller cannot bind the
    label vocabulary and forget the internal domains, which would silently widen
    its scope back to every colleague."""
    return {"aid": account_id, "labels": _KNOWN_LABELS_LOWER,
            "internal": sorted(internal_domains)}


async def _uncategorized_inbox(
    db: Any, account_id: str, limit: int, offset: int = 0,
    internal_domains: frozenset[str] = frozenset(),
) -> list[Any]:
    """One page of mail in :data:`_CLEANUP_SCOPE`, newest first.

    ``offset`` exists because the sweep pages through the whole mailbox and
    messages it categorizes drop OUT of this result set. Rows left behind — the
    ones with no evidence — would otherwise be re-read forever, so the caller
    advances the offset past exactly those.
    """
    return (await db.execute(text(
        f"""SELECT em.id, em.provider_message_id, em.subject,
                  em.from_address, em.received_at, em.unsubscribe_link
             FROM email_messages em
            WHERE {_CLEANUP_SCOPE}
            ORDER BY em.received_at DESC
            LIMIT :limit OFFSET :offset"""
    ), {**_cleanup_scope_params(account_id, internal_domains),
        "limit": limit, "offset": offset})).fetchall()


def _decide(
    row: Any,
    patterns: dict[str, dict[str, list[tuple[str, str]]]],
    rule_labels: dict[str, str],
    by_sender: dict[str, dict[str, int]],
    by_domain: dict[str, dict[str, int]],
    internal_domains: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """(category, reason) for one uncategorized message, or None for no evidence."""
    frm = row.from_address if isinstance(row.from_address, dict) else {}
    sender = (frm.get("email") or "").strip().lower()
    if not sender:
        return None
    # _pattern_hit expects the same email dict shape the rule engine matches on.
    email = {"from": sender, "subject": row.subject or ""}

    # 1. A learned pattern already pins this sender/subject to a labelling rule.
    for rid, cat in rule_labels.items():
        p = patterns.get(rid)
        if not p:
            continue
        if any(_pattern_hit(pt, email) for pt in p["exclude"]):
            continue  # the user taught us this one is NOT that rule
        if any(_pattern_hit(pt, email) for pt in p["include"]):
            return cat, "learned pattern"

    # 2. This sender's own labelled history agrees on one category.
    cat = _consensus(by_sender.get(sender, {}),
                     _SENDER_MIN_LABELLED, _SENDER_MIN_DOMINANCE)
    if cat:
        return cat, "sender history"

    # 3. Fall back to the sending domain — never a shared host, and never the
    #    user's OWN domain. Your employer's domain is a shared domain: every
    #    colleague sends from it, so "same domain" implies nothing about the
    #    sender. Observed live on a real mailbox: a dozen automated @company
    #    alerts labelled Notification sat six messages short of forming a
    #    consensus that would have stamped Notification across thousands of
    #    internal colleague emails. Sender-level evidence (step 2) still
    #    applies to a specific internal address — only the blanket is refused.
    dom = _domain_of(sender)
    if dom and dom not in _SHARED_DOMAINS and dom not in internal_domains:
        cat = _consensus(by_domain.get(dom, {}),
                         _DOMAIN_MIN_LABELLED, _DOMAIN_MIN_DOMINANCE)
        if cat:
            return cat, f"domain history ({dom})"

    # 4. No history anywhere — fall back to what the message itself declares.
    #    LAST deliberately: every step above is evidence the user or the rules
    #    produced, and none of their decisions changes because this exists. This
    #    only reaches mail that was previously getting nothing at all.
    shape = _shape_category(sender, bool(getattr(row, "unsubscribe_link", None)))
    if shape and not _taught_otherwise(shape[0], email, patterns, rule_labels):
        return shape
    return None


def _taught_otherwise(
    category: str,
    email: dict[str, str],
    patterns: dict[str, dict[str, list[tuple[str, str]]]],
    rule_labels: dict[str, str],
) -> bool:
    """Has the user explicitly taught us this message is NOT ``category``?

    Step 1 honours exclude patterns by skipping the rule; the shape step has no
    rule to skip, so it has to ask separately. Without this, teaching "deals@ is
    not Marketing" via Fix would be silently overridden by the local part being
    ``deals@`` — the exclude would look like it had been ignored, which is the
    difference between the cleaner learning and the cleaner nagging.

    A guess must always yield to an instruction.
    """
    for rid, cat in rule_labels.items():
        if cat != category:
            continue
        p = patterns.get(rid)
        if p and any(_pattern_hit(pt, email) for pt in p["exclude"]):
            return True
    return False


async def sweep_uncategorized(
    account_id: str, limit: int, *, dry_run: bool, owner: str = "",
    max_apply: int | None = None, job_token: int | None = None,
) -> dict[str, Any]:
    """Project existing categorization onto uncategorized mail.

    Pages through the mailbox until it runs out of uncategorized mail or hits
    ``limit`` messages scanned. It does NOT stop after one page: a cleaner that
    quietly handles the first N and reports success is worse than one that
    refuses, because the user stops looking.

    ``max_apply`` bounds LABELS WRITTEN, not rows read, and is what the
    per-cycle scheduler run should use. Reading a page is a single indexed
    query; writing a label is a provider round-trip, so they are not remotely
    the same cost and must not share one budget.

    Bounding the scan instead is a trap, because this query is ordered newest
    first and every run restarts at offset 0. A block of no-evidence mail at the
    top is therefore re-read on every cycle and nothing behind it is ever
    reached. Observed in production: ``scanned: 100, applied: 0,
    no_evidence: 100`` repeating every five minutes with 575 older messages
    waiting behind the wall.

    Returns a summary: how many were matched, the per-category breakdown, and
    how many carry no evidence at all (those need an actual rules run — see
    ``/email/rules/process-past``). ``exhausted`` says whether the mailbox
    actually ran dry, so the caller never implies "done" when it means "stopped".

    EM-T1b-2 (``email_app_master_plan.md`` §10.4.2): the sweep runs in phases,
    and each phase is its own ``_tenant_session()`` with the ambient tenant.
    The sync loop binds it for the scheduler sweep, and the request binds it
    for the BackgroundTask jobs and the dry run. No phase calls ``commit()``.
    Phase 0 reads the evidence. The provider authenticates with no session
    open. Each page reads in its own block, and each decided message applies
    in its own block. A last block persists rotated credentials.
    """
    summary: dict[str, Any] = {
        "scanned": 0, "categorized": 0, "no_evidence": 0, "failed": 0,
        "by_category": {}, "by_reason": {}, "dry_run": dry_run,
        "exhausted": False,
        # True when the run stopped because it hit max_apply, i.e. there is more
        # to do and the next cycle will pick it up. Distinct from `exhausted`,
        # which means the mailbox actually ran dry.
        "apply_capped": False,
    }
    provider = None
    store = None
    try:
        async with _tenant_session() as db:
            # Approved INCLUDE patterns only. A pattern is projected across
            # every matching message in the mailbox, with archive/unsubscribe/
            # delete offered on top of the result, so an unreviewed machine-
            # inferred generalisation is not a safe thing to run at that scale.
            # Excludes are unaffected — they only ever prevent a label. See
            # _load_rule_patterns.
            patterns = await _load_rule_patterns(
                db, account_id, approved_includes_only=True)
            rule_labels = await _rule_label_by_id(db, account_id)
            # Tallies are read ONCE and held for the whole sweep. Refreshing
            # them per page would let the sweep's own projections become
            # evidence for further projections — one wrong guess compounding
            # into a category applied across the mailbox. Evidence stays what
            # the user and the rules decided, never what this run decided.
            by_sender, by_domain = await _label_tallies(db, account_id)
            # Your own company domain is a shared domain — see _decide step 3.
            internal_domains = await _internal_domains(db, account_id)

            if not dry_run:
                # Unscoped loader (scheduler ticks + manual sweeps share this
                # job; there is no request user here). The sweep keeps its own
                # explicit auth/persist choreography — the auth-failure verdict
                # goes into ``summary`` and the rotated-cred persist
                # deliberately happens only on the success tail (see the end of
                # this function).
                try:
                    provider, store, _owner = await _provider_for_account_any(
                        db, account_id)
                except HTTPException:
                    summary["error"] = "account not found"
                    return summary

        # The block above ended before the network round-trip. A session left
        # `idle in transaction` across a provider call holds ACCESS SHARE on
        # everything it touched, which is how a migration's ALTER TABLE ends up
        # queued behind a sweep — see the 2026-08-06 write-up.
        if provider is not None and not await provider.authenticate():
            # ABORT — do NOT continue with provider=None. A local-only label
            # is logged APPLIED but Outlook (categories-authoritative) wipes
            # it on the next sync, so the message re-enters scope and the
            # sweep re-applies it every cycle, minting APPLIED audit rows
            # that never reached the mailbox. Fail loudly instead.
            summary["error"] = "provider authentication failed"
            provider = None
            return summary

        from gateway.routes.email.automation.runner import (  # noqa: PLC0415
            mirror_label,
            push_label,
        )
        applied = 0
        offset = 0
        while summary["scanned"] < limit:
            if max_apply is not None and applied >= max_apply:
                summary["apply_capped"] = True
                break
            page_size = min(_SWEEP_PAGE, limit - summary["scanned"])
            async with _tenant_session() as db:
                rows = await _uncategorized_inbox(
                    db, account_id, page_size, offset, internal_domains)
            if not rows:
                summary["exhausted"] = True
                break
            summary["scanned"] += len(rows)

            # Decide the page first — a dry run must never touch the provider,
            # and the preview and the apply must agree on the same verdicts.
            decisions: list[tuple[Any, str, str]] = []
            for r in rows:
                verdict = _decide(r, patterns, rule_labels, by_sender,
                                  by_domain, internal_domains)
                if verdict is None:
                    summary["no_evidence"] += 1
                    continue
                cat, reason = verdict
                decisions.append((r, cat, reason))
                summary["by_category"][cat] = summary["by_category"].get(cat, 0) + 1
                summary["by_reason"][reason] = summary["by_reason"].get(reason, 0) + 1

            if dry_run:
                applied += len(decisions)
                # Nothing was written, so nothing drops out of the next page.
                offset += len(rows)
            else:
                page_applied = 0
                for r, cat, reason in decisions:
                    # The per-item try wraps the per-item block, so a failed
                    # item rolls back its own block and never commits.
                    try:
                        # The provider call runs with NO session open (R-b).
                        # It goes first, as `apply_label` orders it, so a
                        # provider failure writes nothing to the database.
                        lbl = await push_label(
                            provider, str(r.id), r.provider_message_id, cat)
                        async with _tenant_session() as db:
                            await mirror_label(db, str(r.id), lbl)
                            frm = (r.from_address
                                   if isinstance(r.from_address, dict) else {})
                            await db.execute(text(
                                """INSERT INTO email_executed_rules
                                     (account_id, rule_id, rule_name, message_id,
                                      provider_message_id, subject, from_address,
                                      status, automated, actions_taken, reason)
                                   VALUES (:aid, NULL, :rname, :mid, :pmid, :subj,
                                           :frm, 'APPLIED', true, '["LABEL"]',
                                           :reason)"""
                            ), {"aid": account_id,
                                "rname": f"Email Cleaner · {cat}",
                                "mid": str(r.id), "pmid": r.provider_message_id,
                                "subj": r.subject or "",
                                "frm": frm.get("email", ""),
                                "reason": f"Categorized as {cat} from {reason}."})
                        applied += 1
                        page_applied += 1
                        _sweep_tick(account_id, applied, summary["scanned"],
                                    token=job_token)
                    except Exception as exc:  # noqa: BLE001 — one bad message
                        # must not abort the sweep; the rest still gets cleaned.
                        # But it must be COUNTED: `by_category` was incremented at
                        # decision time, `categorized` counts only successful
                        # applies, so without `failed` the two silently disagree
                        # (a throttled run reports a fraction of what it decided).
                        summary["failed"] += 1
                        _log.warning("email.cleanup_apply_failed",
                                     account_id=account_id,
                                     message_id=str(r.id), error=str(exc)[:160])
                # Categorized rows no longer match the query, so they shift the
                # window on their own. Advance past only what stayed behind —
                # the no-evidence rows and any that failed to apply.
                offset += len(rows) - page_applied
            if len(rows) < page_size:
                summary["exhausted"] = True
                break
        summary["categorized"] = applied
        if dry_run:
            return summary
        if provider is not None and store is not None:
            async with _tenant_session() as db:
                await _persist_rotated_creds(db, store, account_id, provider)
        _log.info("email.cleanup_sweep", account_id=account_id, owner=owner,
                  scanned=summary["scanned"], applied=applied,
                  no_evidence=summary["no_evidence"])
        return summary
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.cleanup_sweep_failed", account_id=account_id,
                     error=str(exc)[:200])
        summary["error"] = str(exc)[:200]
        return summary


def _sweep_tick(account_id: str, applied: int, scanned: int = 0,
                token: int | None = None) -> None:
    job = _SWEEP_JOBS.guarded(account_id, token)
    if job:
        job["applied"] = applied
        if scanned:
            job["scanned"] = scanned


async def _sweep_job(
    account_id: str, limit: int, owner: str, token: int | None = None,
) -> None:
    """Background wrapper that keeps the polled progress row up to date.

    ``token`` guards every write: if a newer sweep/backfill superseded this one
    while it ran, these updates no-op instead of overwriting the newer row."""
    try:
        summary = await sweep_uncategorized(
            account_id, limit, dry_run=False, owner=owner, job_token=token)
        # sweep_uncategorized swallows its own exceptions into summary["error"]
        # and returns normally, so a run that died on page 1 must NOT be stamped
        # "done" here — the UI would show "Categorized N emails" for a failed run.
        status = "error" if summary.get("error") else "done"
        _SWEEP_JOBS.finish(
            account_id, token, **summary, owner=owner, status=status,
            finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001
        _SWEEP_JOBS.finish(
            account_id, token, owner=owner, status="error",
            error=str(exc)[:200])


async def restore_provider_labels(account_id: str) -> dict[str, Any]:
    """Re-read every label from the provider and write it back to ``categories``.

    The repair path for a mailbox whose stored labels were lost. Labels live
    upstream — ``apply_label`` always writes the provider first — so the truth
    was never destroyed, only our copy of it. This reads it back in roughly one
    request per label (see BaseProvider.fetch_label_assignments) instead of
    forcing a deep re-sync that would re-download a year of message bodies.

    Only messages the provider reports labels FOR are touched. A message with no
    upstream labels is left exactly as-is rather than cleared, so a label that
    only ever existed locally isn't destroyed by the very job meant to repair.
    """
    out: dict[str, Any] = {"messages": 0, "labels": 0, "updated": 0}
    try:
        async with _tenant_session() as db:
            acc = (await db.execute(text(
                "SELECT provider, credentials_encrypted FROM email_accounts "
                "WHERE id = :id"
            ), {"id": account_id})).fetchone()
            if not acc:
                return out
            import json  # noqa: PLC0415

            from acb_llm.key_store import get_key_store  # noqa: PLC0415
            store = get_key_store()
            creds = json.loads(store.decrypt(acc.credentials_encrypted))
            provider = _instantiate_provider(acc.provider, creds)
            # Say so BEFORE authenticating: on a provider that can't list messages
            # per label, an empty result is indistinguishable from "your mailbox
            # has no labels". Reporting the latter tells a user with thousands of
            # labelled messages that they have none — the opposite of the truth
            # this repair path exists to restore.
            if not getattr(provider, "SUPPORTS_LABEL_READBACK", False):
                out["error"] = "unsupported"
                out["provider"] = acc.provider
                return out
            if not await provider.authenticate():
                out["error"] = "auth-failed"
                return out

            assignments = await provider.fetch_label_assignments()
            out["messages"] = len(assignments)
            out["labels"] = len({lbl for v in assignments.values() for lbl in v})
            # Group by label-set and write one statement per DISTINCT set rather
            # than one per message. A mailbox has a handful of label combinations
            # and tens of thousands of messages, so this is ~20 round-trips instead
            # of 40,000 — the difference between a route that returns and one that
            # times out on exactly the large mailbox that needs it most.
            # Same canonicalisation the sync ingest applies (renamed labels map to
            # their new names, the "Uncategorized" indicator is dropped) — this
            # repair writes raw provider label sets, so without it a restore would
            # resurrect exactly the stale names ingest exists to retire.
            from email_ingestion.persist import _canon_categories  # noqa: PLC0415
            by_labels: dict[tuple[str, ...], list[str]] = {}
            for pmid, labels in assignments.items():
                by_labels.setdefault(tuple(_canon_categories(labels)), []).append(pmid)
            for labels_key, pmids in by_labels.items():
                res = await db.execute(text(
                    "UPDATE email_messages SET categories = :cats, updated_at = now() "
                    "WHERE account_id = :aid AND provider_message_id = ANY(:pmids) "
                    "AND categories IS DISTINCT FROM :cats"
                ), {"aid": account_id, "pmids": pmids, "cats": list(labels_key)})
                out["updated"] += res.rowcount or 0
            await _persist_rotated_creds(db, store, account_id, provider)
            _log.info("email.restore_labels", account_id=account_id, **{
                k: v for k, v in out.items() if isinstance(v, int)})
            return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.restore_labels_failed", account_id=account_id,
                     error=str(exc)[:200])
        out["error"] = str(exc)[:200]
        return out


class RestoreLabelsRequest(BaseModel):
    account_id: str


@router.post("/cleanup/restore-labels")
async def restore_labels(
    req: RestoreLabelsRequest,
    user: UserContext = Depends(get_current_user),
):
    """Repair locally-lost categories by re-reading them from the provider.

    Synchronous — it is a handful of list calls, not a per-message fetch — so the
    caller gets the real counts back rather than having to poll.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
    return await restore_provider_labels(req.account_id)


class CleanupBackfillRequest(BaseModel):
    account_id: str
    # ISO date (YYYY-MM-DD) to fetch back to. None = the whole mailbox.
    since_date: str | None = None


async def _mark_history_held_back(
    db: Any, account_id: str, job_started: datetime,
) -> int:
    """Hold backfilled history back from the model, WITHOUT running the rules.

    This is the whole reason a history backfill is safe to offer. The scheduled
    rule run classifies uncategorized inbox mail 50 messages per cycle, one model
    call each. Tens of thousands of newly downloaded messages all arrive
    unprocessed, so a naive backfill would silently queue tens of thousands of
    model calls — the exact opposite of the point, which is to categorize old
    mail for free.

    THE FLOOR IS THE JOB, NOT THE MAILBOX. The first version of this used
    ``MIN(received_at)`` over the account as the floor, on the theory that
    anything older than the oldest message held must be newly downloaded. One
    stray old message defeats that entirely: on the live account MIN was
    2019-06-28 — a single item in Trash — so the stamp would have matched almost
    nothing and the whole guarantee would have been inert.

    ``created_at`` is the row's insert time (never in the upsert's DO UPDATE SET,
    so a re-sync preserves it), which identifies exactly the rows this backfill
    inserted. Pairing it with ``received_at < job_started`` keeps genuinely NEW
    mail eligible: a message that arrives mid-backfill is inserted during the job
    but was received after it began, so it is not history and is not held back.

    Held back is NOT the same as processed — see migration 84. The rules never
    saw these messages, so "Process past emails" can still reach them on request;
    only the automatic, unbounded, per-cycle run is kept off them. The
    deterministic sweep ignores the column entirely, so the mail stays fully
    cleanable — just not by the model.

    No commit here: the caller holds a ``_tenant_session`` block, and the seam
    commits on its clean exit (EM-T4a-0).
    """
    res = await db.execute(text(
        "UPDATE email_messages SET rules_held_back_at = now() "
        " WHERE account_id = :aid "
        "   AND rules_processed_at IS NULL AND rules_held_back_at IS NULL "
        "   AND created_at >= :started AND received_at < :started"
    ), {"aid": account_id, "started": job_started})
    return int(getattr(res, "rowcount", 0) or 0)


async def _backfill_and_clean_job(
    account_id: str, since: datetime | None, owner: str,
    token: int | None = None,
) -> None:
    """Download older mail, then categorize it deterministically.

    Two phases behind one progress row, because to the user it is one action:

    1. ``downloading`` — a deep provider sync from ``since`` (None = everything).
       The first sync of an account only ever fetched 365 days
       (``INITIAL_SYNC_DAYS``) and every sync after it is incremental, so on a
       real mailbox most mail has simply never been seen locally. Measured on the
       live account: 6,803 messages held against ~43,000 in the mailbox.
    2. ``cleaning`` — the ordinary sweep over everything now present. No model
       calls, so the cost of this phase does not grow with the backlog; and it
       gets BETTER with history, because sender and domain consensus finally have
       enough labelled mail to fire.

    Between the two, the downloaded history is held back from the model-driven
    rule run — without being marked rules-processed, so a deliberate "Process
    past emails" run can still reach it (migration 84).

    EM-T4a-0 (``email_app_master_plan.md`` §10.4.6): the backfill route starts
    this as a BackgroundTask, which runs inside the tenant scope of its
    request. Each count is its own ``_tenant_session()`` block with that
    ambient tenant, and the sync runs with no session of ours open. The
    second block also holds the history back, and the seam commits it. With
    no tenant bound, the first block raises ``TenantUnbound``, and the
    handler below records it on the progress row.
    """
    # The "running" row was seeded (and the token minted) by the endpoint, so a
    # second request sees this run in-flight before this task even starts.
    started = datetime.now(timezone.utc)
    try:
        async with _tenant_session() as db:
            row = (await db.execute(text(
                "SELECT COUNT(*) AS n FROM email_messages WHERE account_id = :aid"
            ), {"aid": account_id})).fetchone()
            count_before = int(getattr(row, "n", 0) or 0)

        from email_ingestion.scheduler import _sync_account  # noqa: PLC0415
        # Waits for a sync of this mailbox that runs now, with no session
        # open, then runs (EM-T4f part 2).
        await _sync_account(account_id, deep=True, since=since)

        async with _tenant_session() as db:
            after = (await db.execute(text(
                "SELECT COUNT(*) AS n FROM email_messages WHERE account_id = :aid"
            ), {"aid": account_id})).fetchone()
            fetched = max(0, int(getattr(after, "n", 0) or 0) - count_before)
            held_back = await _mark_history_held_back(db, account_id, started)

        _SWEEP_JOBS.update(
            account_id, token, phase="cleaning", synced=fetched,
            held_back=held_back)
        _log.info("email.cleanup_backfill_synced", account_id=account_id,
                  fetched=fetched, held_back=held_back)

        summary = await sweep_uncategorized(
            account_id, _MAX_SWEEP, dry_run=False, owner=owner, job_token=token)
        _SWEEP_JOBS.finish(
            account_id, token, **summary, owner=owner, status="done",
            phase="done", synced=fetched, held_back=held_back,
            finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001
        _SWEEP_JOBS.finish(
            account_id, token, owner=owner,
            status="error", phase="error", error=str(exc)[:200])
        _log.warning("email.cleanup_backfill_failed", account_id=account_id,
                     error=str(exc)[:200])


@router.post("/cleanup/backfill")
async def cleanup_backfill(
    req: CleanupBackfillRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Fetch older mail from the provider, then clean it without a model.

    The Email Cleaner can only clean what has been synced, and the initial sync
    only ever reached back one year. This is the deterministic counterpart of
    "Process past emails": same shape, but it spends no model calls — it fetches
    history, holds it back from the model-driven rule run, and lets the sweep
    project learned patterns and sender/domain history onto it.

    Runs in the background; poll ``GET /email/cleanup/status``.
    """
    owner = user.email or "anonymous"
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, owner)

    since = None
    if req.since_date:
        from gateway.routes.email.core import _parse_iso_date  # noqa: PLC0415
        since = _parse_iso_date(req.since_date, end_of_day=False)

    if _SWEEP_JOBS.is_running(req.account_id):
        # Two concurrent deep syncs on one mailbox would race the provider and
        # each other's progress row; say so rather than silently start a second.
        return {"scheduled": False, "reason": "already_running"}

    # Seed the row (and mint the guard token) synchronously, BEFORE returning, so
    # a second request racing this one sees the run in-flight.
    token = _SWEEP_JOBS.start(
        req.account_id, owner=owner, status="running", phase="downloading",
        applied=0, scanned=0, synced=0, held_back=0,
        started_at=datetime.now(timezone.utc).isoformat())
    background.add_task(
        _backfill_and_clean_job, req.account_id, since, owner, token)
    return {"scheduled": True, "since": req.since_date}


class CleanupSweepRequest(BaseModel):
    account_id: str
    # Max messages to SCAN. 0 (the default) means "the whole mailbox" — the
    # cleaner's job is to finish, not to nibble. A caller can still pass a
    # smaller number to take a bite.
    limit: int = 0
    # Preview only: decide everything, touch nothing. Cheap (no provider calls),
    # so the UI can show "N will be categorized" before the user commits.
    dry_run: bool = False


@router.post("/cleanup/auto-categorize")
async def auto_categorize_inbox(
    req: CleanupSweepRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Fill in categories for uncategorized inbox mail from existing evidence.

    Projects learned patterns and per-sender/per-domain label history onto mail
    the rules never reached. Runs no classifier of its own — anything without
    evidence is reported in ``no_evidence`` and needs a real rules run.

    ``dry_run`` returns the verdicts synchronously without writing, over a
    bounded sample of the most recent mail so the request stays fast; it sets
    ``sampled`` when it stopped early. A live run covers the WHOLE mailbox and
    is scheduled in the background (it writes to the provider per message); poll
    ``GET /email/cleanup/status``.
    """
    owner = user.email or "anonymous"
    # limit=0 means "everything"; _MAX_SWEEP is only a runaway backstop.
    limit = min(req.limit, _MAX_SWEEP) if req.limit > 0 else _MAX_SWEEP
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, owner)

    if req.dry_run:
        preview_limit = min(limit, _PREVIEW_MAX)
        res = await sweep_uncategorized(
            req.account_id, preview_limit, dry_run=True, owner=owner)
        # Be explicit when the preview only saw part of the mailbox, so the UI
        # never presents a sample's numbers as the full picture.
        res["sampled"] = not res.get("exhausted", False)
        return res

    if _SWEEP_JOBS.is_running(req.account_id):
        # A sweep (or a backfill's cleaning phase) is already running for this
        # mailbox. Two would race the provider and clobber each other's progress
        # row — the exact class the shared token guard closes. Refuse, matching
        # the backfill endpoint, rather than silently start a second.
        return {"scheduled": False, "reason": "already_running"}

    # Seed + mint the guard token synchronously so a racing request sees it.
    token = _SWEEP_JOBS.start(
        req.account_id, owner=owner, status="running", applied=0,
        started_at=datetime.now(timezone.utc).isoformat())
    background.add_task(_sweep_job, req.account_id, limit, owner, token)
    return {"scheduled": True}


@router.get("/cleanup/status")
async def cleanup_status(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Progress of the most recent auto-categorize sweep on this account."""
    job = _SWEEP_JOBS.get(account_id)
    if not job or job.get("owner") != (user.email or "anonymous"):
        return {"status": "idle"}
    return {k: v for k, v in job.items() if k != "owner"}


@router.get("/cleanup/uncategorized")
async def uncategorized_overview(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """How much mail is outstanding cleanup material, and who it's from.

    Powers the Email Cleaner's "Uncategorized" tab: the count drives the badge,
    the sender breakdown tells the user whether one noisy sender is the problem.

    Counts exactly :data:`_CLEANUP_SCOPE` — the same rows the sweep will work
    on. It used to count every message with no known label anywhere in the
    mailbox, including Sent and every colleague, so the badge showed 4,391 while
    the sweep could only ever act on a fraction of it. A backlog number that
    cannot reach zero teaches the user to ignore the badge.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        internal_domains = await _internal_domains(db, account_id)
        params = _cleanup_scope_params(account_id, internal_domains)
        total = (await db.execute(text(
            f"SELECT COUNT(*) AS c FROM email_messages em WHERE {_CLEANUP_SCOPE}"
        ), params)).fetchone()
        rows = (await db.execute(text(
            f"""SELECT LOWER(em.from_address->>'email') AS email,
                      MAX(em.from_address->>'name') AS name,
                      COUNT(*) AS n
                 FROM email_messages em
                WHERE {_CLEANUP_SCOPE}
                GROUP BY 1 ORDER BY n DESC LIMIT 25"""
        ), params)).fetchall()
        # Patterns waiting for review, and what approving them would reach. The
        # cleaner's strongest evidence is a learned pattern, and it will not
        # project an unreviewed one (see _load_rule_patterns) — so without this
        # the cleaner would simply be quietly worse than it used to be, with
        # nothing on screen explaining why or how to fix it.
        pending = (await db.execute(text(
            "SELECT COUNT(*) AS c FROM email_rule_patterns "
            "WHERE account_id = :aid AND exclude = false "
            "  AND approved_at IS NULL AND rejected_at IS NULL"
        ), {"aid": account_id})).fetchone()
        reach = (await db.execute(text(
            f"""SELECT COUNT(DISTINCT em.id) AS c
                  FROM email_messages em
                  JOIN email_rule_patterns p
                    ON p.account_id = em.account_id
                   AND p.exclude = false
                   AND p.approved_at IS NULL AND p.rejected_at IS NULL
                   AND ((p.pattern_type = 'SUBJECT'
                         AND LOWER(COALESCE(em.subject, ''))
                             LIKE '%' || LOWER(p.value) || '%')
                     OR (p.pattern_type <> 'SUBJECT'
                         AND LOWER(COALESCE(em.from_address->>'email', ''))
                             LIKE '%' || LOWER(p.value) || '%'))
                 WHERE {_CLEANUP_SCOPE}"""
        ), params)).fetchone()
        return {
            "uncategorized": int(total.c) if total else 0,
            "pending_patterns": int(pending.c) if pending else 0,
            "pending_pattern_reach": int(reach.c) if reach else 0,
            "top_senders": [
                {"email": r.email, "name": r.name or "", "count": int(r.n)}
                for r in rows
            ],
        }
