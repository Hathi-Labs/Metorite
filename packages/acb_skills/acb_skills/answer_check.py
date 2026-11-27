"""The number check of a chat answer that read a CRM tool (WS-53 CRM-Q29).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q29.

SQL gives each CRM number (CRM-Q25), but the model writes the sentence around
it. So the model can misquote a number, round it wrongly, add two results
itself or invent one. This module checks each number of an answer against the
numbers that the run's tools gave, before the member reads the answer.

**The seam.** ``orchestrator._tool_injection._inject_agent_tools`` puts
:class:`AnswerCheckProvider` on the per-run view of a native MAF agent that
holds a CRM tool. That covers the stream run, the batch run and the
sub-agent. The provider adds two middlewares to ONE run:

* :class:`RecordToolResults` (function) keeps the text of each tool result of
  the run, and the arguments of each CRM tool call, in memory. A CRM tool
  result arms the check.
* :class:`CheckAnswers` (chat) does nothing until the check arms. Then, with
  the mode ``on``, it holds each model response until the response ends, and
  checks its text and the shown text of each display call (a card, a report,
  a document, a WhatsApp element) with ``acb_common.number_check.check``.

**The sources (item 4).** The tool results of the run (no delegated answer,
which a model wrote), the arguments of the run's CRM tool calls (a date gives
its count of days to today), and the member's own words of this turn
(:data:`MEMBER_MESSAGE` in the run context). Never the packed prompt string,
which holds the earlier answers of the model.

**The behaviour on a mismatch (item 7).** The first failed answer of a run
gets ONE more model request, which names each number that matched no source.
The check reads that answer too. A second failure, or a failure after the run
used its retry, sends the answer with a note of :func:`note_text` (round 8).
The note never lists raw figures alone: a sum or a difference of two figures
of the answer gets one sentence, many failed figures get "How I got these
numbers" from the run's CRM reads, an ambiguity that the check can see gets
ONE question, and one to three failed figures each get a reason. An answer
with no text gets :func:`fallback_text`. A failed step that calls only tools
that show nothing loses its text and keeps its tool calls. So a run makes one
extra model request at most, and a passed check makes none.

**The hold and the watchdog (item 10).** While it holds a response, the check
puts a :data:`HOLD_EVENT` on the run queue at most each
:data:`HEARTBEAT_S` seconds, and only when the model sent an update. The
executor reads it as a sign of life, not as output, and shows nothing. A
model that stops sending still meets the idle watchdog.

**The mode.** ``acb_common.settings.crm_answer_check_mode`` is the one reader
of ``CRM_ANSWER_CHECK``: ``off`` (the default), ``shadow`` or ``on``.
``shadow`` checks each armed response at its end and logs, with no hold and
no retry.

No log line holds a number or a text. Fence: ``tests/unit/test_crm_answer_check.py``.

**The "not found" check** (WS-FIND FIND-7, ``find_engine.md`` §5.5). The
same provider adds :class:`CheckClaims` to a run of an agent that holds
``search_company``, when ``FIND_CLAIM_CHECK`` is not ``off``
(``acb_common.settings.find_claim_check_mode``, the one reader). It reads the
final answer of each model request (a response with no tool call). An answer
that :func:`not_found_claim` reads as "the record does not exist" needs a
``search_company`` answer in the same run that shows pass 2
(``company_search_tools.ran_second_pass``).

* ``shadow`` logs one ``answer_check.claim`` line for the run, and changes
  nothing.
* ``on`` holds each answer once the run read a result of any tool of
  :data:`FIND_TOOLS`. A claim with no pass 2 gets ONE more model request
  with :data:`CLAIM_RETRY_NOTE`, after an app's search tool alone too. That
  is the one retry of the run, which the number check spends too
  (``RunLedger.retried``). After it, the answer goes as it is (D-FIND-8). A
  retry that raises sends the held draft as it is (``retry_failed``). An
  answer before any find result is not held: the check logs it as
  ``unheld_miss``.

The seam attaches the check only for an org of ``FIND_ORGS``, because pass 2
exists only there (``orchestrator._tool_injection._apply_answer_check``).

The claim line holds :data:`CLAIM_LOG_FIELDS`: the mode, the outcome, the
rule code, two counts and ``armed``. ``armed`` is true when a
``search_company`` or ``resolve_name`` answer is in the run
(:data:`CLAIM_ARM_TOOLS`), and false when only an app's search tool ran, or
none. It holds no text of the answer and no
word of a record. The matcher reads the first :data:`MAX_CLAIM_CHARS`
characters, so its work is bounded, because it runs on the event loop. Fence:
``tests/unit/test_answer_not_found.py``.
"""

from __future__ import annotations

import asyncio
import bisect
import functools
import json
import re
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from acb_common import get_logger
from acb_common.number_check import (
    EMPTY_LABELS,
    MAX_EXPLAIN,
    MONEY,
    PERCENT,
    CheckResult,
    Figure,
    Sources,
    arithmetic,
    check,
    find_figures,
    group_indian,
)
from acb_common.settings import crm_answer_check_mode
from acb_common.tool_refusals import is_refusal
from agent_framework import (
    ChatMiddleware,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    ContextProvider,
    FunctionMiddleware,
    Message,
    ResponseStream,
)

from acb_skills.company_search_tools import ran_second_pass
from acb_skills.crm_tools import CRM_TOOL_NAMES
from acb_skills.egress import DELEGATION_TOOLS, is_delegation_tool
from acb_skills.tool_guard import tool_name

_log = get_logger("acb_skills.answer_check")

SOURCE_ID = "crm_answer_check"

OFF = "off"
SHADOW = "shadow"
ON = "on"
MODES: tuple[str, ...] = (OFF, SHADOW, ON)

#: The one reader of ``CRM_ANSWER_CHECK`` lives in ``acb_common.settings``.
answer_check_mode = crm_answer_check_mode

#: The four reads of ``crm-assistant`` (``apps/agents/agent-crm``). They are
#: CRM tools too, so their results arm the check.
CRM_ASSISTANT_READS: frozenset[str] = frozenset({
    "search_crm", "get_pipeline", "get_record", "get_timeline",
})

#: A result of one of these tools arms the check (item 5).
ARM_TOOLS: frozenset[str] = CRM_TOOL_NAMES | CRM_ASSISTANT_READS

#: The tools whose results the fallback shows. Each is a figure tool, and
#: none of them returns text that a stranger wrote.
FALLBACK_TOOLS: frozenset[str] = frozenset({
    "crm_aggregate", "crm_report", "crm_stage_dwell", "get_pipeline",
})

#: The tools whose ARGUMENTS the member reads: a card, a report, a document,
#: a text file, a WhatsApp element (``whatsapp_ui``, fix round 3), and the
#: question and confirmation cards (``ask_questions``,
#: ``request_confirmation``, fix round 4). The check reads their shown text
#: as it reads the answer.
DISPLAY_TOOLS: frozenset[str] = frozenset({
    "emit_generative_ui", "make_report", "make_document", "write_artifact",
    "whatsapp_ui", "ask_questions", "request_confirmation",
})

#: The memory reads. They show nothing and send nothing.
RECALL_TOOLS: frozenset[str] = frozenset({
    "remember", "recall_timeline", "recall_agent", "recall_org", "recall_notes",
})

#: The ONLY tools that a failed step may call and still go out without its
#: text (fix round 4). Each one shows the member nothing and sends nothing:
#: the CRM reads and the memory reads. A failed step that calls any other
#: tool fails, so a card, a file, a message or a tool that this list does
#: not know never ships beside a failed text. A new quiet tool joins here.
QUIET_TOOLS: frozenset[str] = ARM_TOOLS | RECALL_TOOLS

#: A tool result that a model wrote, or a script that a model wrote printed.
#: It is no source (fix round 4): ``code_task`` runs a coding agent, as a
#: delegation does, and the others print what the model's own code prints.
#: So a number that only such a result holds fails the check.
MODEL_WRITTEN_TOOLS: frozenset[str] = DELEGATION_TOOLS | frozenset({
    "code_task", "run_script", "run_command", "code_task_run_command",
})

#: A result longer than this many characters is read on a worker thread, so
#: a result with 100,000 figures does not hold the event loop (fix round 4).
RECORD_INLINE_CHARS = 20_000

#: ``write_artifact`` shows text only for these files. A script is code.
TEXT_FILE_SUFFIXES: tuple[str, ...] = (".md", ".txt", ".html", ".htm", ".csv")

#: The keys of a card or report spec whose number is layout, not a figure.
LAYOUT_KEYS: frozenset[str] = frozenset({
    "width", "height", "span", "col_span", "colspan", "rowspan", "cols",
    "size", "level", "order", "index", "position", "version", "gap", "padding",
    "margin", "radius", "opacity", "font_size", "fontsize", "decimals",
    "precision", "limit", "page", "page_size", "z_index", "zindex", "duration",
    "delay", "step", "max_items", "min_width", "max_width",
})

#: The keys of a spec whose text is not shown: a kind, an id, a style, a link.
HIDDEN_KEYS: frozenset[str] = frozenset({
    "type", "id", "key", "icon", "color", "colour", "variant", "surface",
    "template", "component", "classname", "class", "style", "href", "url",
    "src", "format", "path", "encoding", "hitl", "kind", "slug",
})

#: The key in the run context that holds the member's own words of this turn.
MEMBER_MESSAGE = "member_message"
#: The most characters of the member's words that the run context keeps.
MEMBER_MESSAGE_CHARS = 20_000

#: The CUSTOM event that says "the check holds an answer, the model is busy".
HOLD_EVENT = "answer_check.held"
#: The least time between two :data:`HOLD_EVENT` events of one run.
HEARTBEAT_S = 15.0

#: The most numbers that the retry request names.
MAX_NAMED = 8
#: The newest results of :data:`FALLBACK_TOOLS` that the fallback shows.
FALLBACK_RESULTS = 3
#: The most characters of one result in the fallback.
FALLBACK_CHARS = 2000

#: What the member reads in place of an answer that failed the check.
FALLBACK_LEAD = (
    "I could not match each number of my answer to the CRM data, so I do not "
    "show that answer."
)
FALLBACK_FIGURES = "These are the CRM figures as the tools gave them:"
FALLBACK_NONE = "Ask again, or ask for one figure at a time."

# ── The note after an answer that still failed the check ───────────────────
# Round 7 (owner direction): give the number with a note, never lose the
# answer. Round 8 (owner, 2026-10-20): the note never lists raw figures
# alone. Plain words, no internal names, money in lakh and crore.

#: The most failed figures that the note names, each with a reason.
NAMED_MAX = 3
#: More failed figures than this share of all figures is a limit of the
#: check, not of the answer. The note then names no figure.
MANY_SHARE = 0.3
#: The share rule needs this many failed figures at least. One failed
#: figure of three is a real doubt about that figure, so it gets its reason.
MANY_MIN_FAILED = 2
NOTE_MISSING = ("I could not find {figure} in the CRM results. It may be my own "
                "calculation. Ask me how I got it, or check it in the CRM.")
NOTE_NEAR = ("I could not find {figure} in the CRM results. The nearest CRM figure is "
             "{near}, so I may have rounded it wrongly. Check it in the CRM before you "
             "rely on it.")
NOTE_PERCENT = ("I could not work out {figure} from the CRM results. Ask me which "
                "figures it compares.")
NOTE_SUM = "{figure} is {a} {word} {b}."
HOW_HEAD = "How I got these numbers:"
HOW_TAIL = ("Each figure in the answer comes from these CRM results, or from a sum or "
            "a difference of them. Ask me about any figure, and I will show the steps.")
HOW_NONE = ("I could not check many figures of this answer one by one. Ask me about "
            "any figure, and I will show how I got it.")
QUESTION_EMPTY = ("In {module}, {share}% of the {measure} has no {group}. Should I show "
                  'those records as their own group, "No {group}", or group them by '
                  "another field?")
QUESTION_OPEN = ("Some of these figures depend on a question that is still open. Your "
                 "answer to it can change them.")
#: The least share of a total that an empty group must hold before the
#: note asks about it.
EMPTY_SHARE = 0.10
#: The CRM reads that "How I got these numbers" names, and how many.
HOW_TOOLS: frozenset[str] = FALLBACK_TOOLS
MAX_READS = 6
#: The kinds of a note, for the log. The log never holds the note itself.
NOTE_NONE = "none"
NOTE_EXPLAINED = "explained"
NOTE_NAMED = "named"
NOTE_HOW = "how"
NOTE_QUESTION = "question"

_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
#: The head line of a grouped CRM total: "Deals, sum of Amount by Region:".
_GROUPED_HEAD = re.compile(r"^(?P<what>[^\n]+?) by (?P<group>[^:\n]+):[ \t]*$")
_LIST_ROW = re.compile(r"^[ \t]*[-*\u2022][ \t]+(?P<label>[^:\n]{0,120}):[ \t]*(?P<value>.+)$")
#: The marks of a CRM question that is still open (CRM-Q32-8, CRM-Q33).
_OPEN_MARK = re.compile(r"\bCLARIFY\b|\bASK FIRST\b|^Not clear:", re.M)
#: The filters of a core CRM read, in plain words.
_FILTER_WORDS: dict[str, str] = {
    "status": "status is", "owner": "owner is", "q": "text matches",
    "created_from": "created from", "created_to": "created to",
    "updated_from": "updated from", "updated_to": "updated to",
    "close_from": "closing from", "close_to": "closing to",
}
_TAG = re.compile(r"<[^>]*>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
#: The shape of code, not a word of prose. "An expected return of ₹5 lakh"
#: and "import duties" are prose, so the check reads their numbers (fix
#: round 3). A string of this shape shows nothing the check can read.
_CODE_LIKE = re.compile(
    r"=>"
    r"|\bfunction\s*\w*\s*\("
    r"|^\s*import\s+[\w{*][^\n]*\bfrom\s+['\"]"
    r"|^\s*(?:import|from)\s+[\w.]+(?:\s+import\b|\s*$)"
    r"|\b(?:const|let|var)\s+\w+\s*="
    r"|\breturn\s*[(<{\[]"
    r"|\breturn\s+[\w.$\[\]]+\s*;",
    re.MULTILINE,
)

# ── The "not found" check (WS-FIND FIND-7, ``find_engine.md`` §5.5) ────────

#: The tool whose answer shows pass 2 (``company_search_tools.ran_second_pass``).
#: An agent that holds it gets the claim check.
CLAIM_SEARCH_TOOL = "search_company"

#: The find tools of the chat (``find_engine.md`` §5.2 and §5.3). The claim
#: line counts their results (``finds``). The first result of one of them
#: arms the hold of the claim check in ``on`` (FIND-7 round 5, the decision of
#: the planner): a claim after an app's search tool alone has no pass 2, so
#: it gets the one retry.
FIND_TOOLS: frozenset[str] = frozenset({
    CLAIM_SEARCH_TOOL, "resolve_name", "search_crm", "crm_list_records",
    "query_inbox", "find_contact", "find_tasks", "resolve_person",
})

#: The tools with a pass 2 or a near ladder of names (``search_company``, and
#: ``resolve_name`` of FIND-5). The claim line says ``armed`` true when one of
#: them answered in the run, so the retry is a fair request to run pass 2.
#: With ``armed`` false, only an app's search tool ran: an empty list ("no
#: tasks due today") may be a true answer, and the owner reads such lines first.
CLAIM_ARM_TOOLS: frozenset[str] = frozenset({CLAIM_SEARCH_TOOL, "resolve_name"})

#: The most characters of an answer that :func:`not_found_claim` reads. A
#: claim after them is not read. It bounds the work on the event loop.
MAX_CLAIM_CHARS = 20_000
#: The most characters of one quote that the matcher reads as a quote.
MAX_QUOTE_CHARS = 400

#: The text of the one more pass (D-FIND-8). It is fixed: no word of the
#: answer, of a record or of the member goes into it.
CLAIM_RETRY_NOTE = (
    "A check of your answer found that it says a record does not exist, or that "
    "you could not find it. No search_company answer of this turn shows its second "
    "pass: each word alone and the near spellings. Before you say that a record "
    "does not exist, call search_company once with all the words of the member's "
    "request: the names, the kind of record and any number. Read how each hit "
    "matched, and read the 'Each word alone' and 'Where else' lines. Then answer "
    "again. If the search still finds nothing, say what you searched, the "
    "spellings it tried and where else to look."
)

#: The rule codes of :func:`not_found_claim`, for the log. A closed list.
CLAIM_RULES: tuple[str, ...] = (
    "no_such", "could_not_find", "no_record_of", "there_is_no_named", "does_not_exist",
    "found_nothing", "not_in_place",
)

#: The outcomes of the claim line, for the log. A closed list.
CLAIM_OUTCOMES: tuple[str, ...] = (
    "shadow_pass", "shadow_miss", "pass", "retry", "retry_spent",
    "after_retry_miss", "unheld_miss", "retry_failed",
)

#: The fields of the claim line. Each value is a code, a count or a bool.
CLAIM_LOG_FIELDS: tuple[str, ...] = ("mode", "outcome", "rule", "searches", "finds", "armed")

#: The kinds of record whose absence an answer can claim. Plain English only.
_RECORD_NOUN = (
    r"(?:orders?|deals?|leads?|contacts?|customers?|clients?|compan(?:y|ies)"
    r"|accounts?|persons?|people|records?|entr(?:y|ies)|invoices?"
    r"|quot(?:e|es|ation|ations)|payments?|e-?mails?|mails?|messages?|threads?"
    r"|conversations?|tasks?|projects?|tickets?|vendors?|suppliers?|shipments?"
    r"|match(?:es)?|results?)"
)
#: The curly quote marks, by code point: a single pair, then a double pair.
_LSQUO, _RSQUO, _LDQUO, _RDQUO = chr(0x2018), chr(0x2019), chr(0x201C), chr(0x201D)
#: A straight and a curly apostrophe, for a class of a pattern.
_APOS = "'" + _RSQUO
#: The quote marks that :func:`_claim_in` takes off a word of the span.
_QUOTE_MARKS = "«»\"'" + _LSQUO + _RSQUO + _LDQUO + _RDQUO
#: "could not", "can't", "didn't", "was not able to", "unable to", with a
#: straight or a curly apostrophe.
_CANNOT = (
    rf"(?:could(?:n[{_APOS}]t|\s+not)|can(?:[{_APOS}]t|not|\s+not)|did(?:n[{_APOS}]t|\s+not)"
    rf"|(?:was|were|am|is|are)(?:n[{_APOS}]t|\s+not)\s+able\s+to|unable\s+to)"
)
#: A word that ends the span between the verb and the record: "find TIME to
#: update the deal" and "find a WAY to export the deal" are no claim. A
#: preposition ends it too: "find any errors IN the invoice" and "find any
#: issues WITH the order" say what a record holds, not that it is missing.
_SPAN_STOP = frozenset({
    "to", "time", "way", "ways", "chance", "moment", "how", "where", "why", "when",
    "what", "which", "who", "whether", "room", "space", "reason", "need", "words",
    "setting", "settings", "option", "options", "button", "field", "fields",
    "with", "in", "on", "of", "for", "about", "at", "from", "by", "into", "within",
    "inside", "regarding", "across", "between", "among", "under", "over", "after",
    "before", "during", "than", "like", "wrong",
    # A list or a check, not a lookup: "no OVERDUE tasks", "no DUPLICATE contacts".
    "overdue", "unread", "upcoming", "due", "duplicate", "duplicates", "duplicated",
})
#: One word of the span, a quoted name too: "find «Neuropeak» in the CRM".
#: A word has 60 characters at most, so a long run of letters ends the span.
_SPAN_WORD = r"[^\s.!?,;:]{1,60}"
#: The places of the company's records: "I could not find Sharath IN THE CRM".
_PLACE = (
    r"(?:crm|inbox|mailbox|mails?|e-?mails?|records|system|database|projects|tasks"
    rf"|people|contacts|whatsapp|knowledge(?:\s+base)?|company(?:[{_APOS}]s)?\s+records)"
)
#: The verb of a failed lookup: "could not find", "can't locate".
_CANNOT_FIND = rf"\b{_CANNOT}\s+(?:find|locate|track\s+down)\b"
#: Each rule of a claim, in order: its code and its pattern. Each pattern
#: needs a record noun, or a NAME (group ``name``, :func:`_is_name`), so "I
#: could not find the time" is no claim.
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("no_such", re.compile(
        rf"\bno\s+such\s+(?:{_SPAN_WORD}\s+){{0,2}}?{_RECORD_NOUN}\b", re.I)),
    ("could_not_find", re.compile(
        rf"{_CANNOT_FIND}(?P<span>(?:\s+{_SPAN_WORD}){{0,4}}?)\s+"
        rf"(?:{_RECORD_NOUN}\b|any(?:thing|one|body)\s+(?:for|about|on|matching|named"
        r"|called|under|from|related|with)\b)",
        re.I)),
    # A name in a place of records: "I could not find Sharath in the CRM".
    ("could_not_find", re.compile(
        rf"{_CANNOT_FIND}(?P<name>(?:\s+{_SPAN_WORD}){{1,4}}?)\s+"
        rf"in\s+(?:the|your|our|any)\s+{_PLACE}\b",
        re.I)),
    # A name that ends the clause: "I could not find Neuropeak."
    ("could_not_find", re.compile(
        rf"{_CANNOT_FIND}(?P<name>(?:\s+{_SPAN_WORD}){{1,3}}?)"
        r"(?=\s*(?:[.!;,]|$)|\s+anywhere\b)",
        re.I)),
    ("no_record_of", re.compile(
        r"\bno\s+(?:records?|match(?:es)?|results?|entr(?:y|ies))\s+"
        r"(?:of|for|matching|named|called|about|from|with|under)\b", re.I)),
    ("there_is_no_named", re.compile(
        rf"\bthere(?:\s+is|\s+are|\s+was|\s+were|[{_APOS}]s)\s+no\s+{_RECORD_NOUN}\s+"
        r"(?:named|called|matching|(?:by|with|under)\s+(?:the|that|this|such\s+a)\s+name)\b",
        re.I)),
    ("does_not_exist", re.compile(
        rf"\b{_RECORD_NOUN}\b[^.!?\n]{{0,60}}?\b(?:does|do|did)(?:n[{_APOS}]t|\s+not)\s+"
        r"(?:seem\s+to\s+|appear\s+to\s+)?exist\b", re.I)),
    # "I found nothing for Sharath." A name must follow, and the clause ends
    # there or goes on to a place of records: "for Sharath in the CRM". So
    # "nothing for Q4 in the forecast" is no claim.
    ("found_nothing", re.compile(
        r"\b(?:found|returned|got)\s+nothing\s+(?:for|about|on|matching|named|called"
        rf"|under|from|with)(?P<name>(?:\s+{_SPAN_WORD}){{1,3}}?)"
        rf"(?=\s*(?:[.!;,]|$)|\s+(?:and|or)\b|\s+in\s+(?:the|your|our|any)\s+{_PLACE}\b)",
        re.I)),
    # "That company is not in the CRM." "Sharath is not in the CRM." The
    # clause ends after the place, so "not in the CRM pipeline stage" is no claim.
    ("not_in_place", re.compile(
        rf"\b(?P<subject>{_RECORD_NOUN}|(?-i:[A-Z][^\s.!?,;:]{{0,59}}))\s+"
        rf"(?:is|are|was|were)(?:n[{_APOS}]t|\s+not)\s+(?:in|on)\s+(?:the|your|our|any)\s+"
        rf"{_PLACE}\b(?=\s*(?:[.!;,]|$))",
        re.I)),
)
#: A clause that makes a claim a condition or a doubt: "If I can't find the
#: order, ..." is no claim.
_HEDGE = re.compile(r"\b(?:if|unless|whether|in\s+case)\b", re.I)
#: The words right before a claim that report what another PERSON said:
#: "Priya replied that there is", "Ravi says he". Group ``who`` is the word
#: right before the verb, and :func:`_is_person` must take it. So "The CRM
#: says there is no such order" stays a claim, and so does "I said".
_REPORTED = re.compile(
    r"(?:^|\s)(?P<who>[^\s.!?,;:]{1,60})\s+(?:says?|said|replied|replies|wrote|writes"
    r"|mentioned|mentions|told\s+\w+|tells\s+\w+|reported|reports|confirmed|confirms"
    r"|claims?|claimed|explained|explains|answered|answers|responded|responds|noted"
    r"|notes)\s+(?:that\s+)?(?:(?:he|she|they|it|we|you|there)\s+)?"
    r"(?:(?:is|are|was|were|has|have|had)\s+)?$",
    re.I,
)
#: The lower-case words that name a person as a subject.
_PERSON_WORDS = frozenset({
    "he", "she", "they", "customer", "client", "vendor", "supplier", "buyer",
    "seller", "manager", "colleague", "team", "sender", "rep", "owner", "member",
    "partner", "accountant", "boss", "lead",
})
#: The words that name the tool, a store of records or the assistant. A claim
#: after them is the claim of a lookup: "The CRM says there is no such order",
#: "Company search reports no matches for Sharath". An app, a product or the
#: assistant is no person either: "Odoo says there is no such order" and
#: "Mira says" stay claims (FIND-7 round 4).
_SYSTEM_WORDS = frozenset({
    "crm", "search", "system", "inbox", "mailbox", "records", "record", "results",
    "result", "database", "tool", "app", "query", "index", "data", "engine", "api",
    "i", "it", "this", "that", "the", "our", "your", "which", "there",
    # The apps of the company and the outside products that hold its records.
    "email", "people", "projects", "tasks", "calendar", "whatsapp", "knowledge",
    "drive", "zoho", "gmail", "salesforce", "odoo", "hubspot", "outlook", "slack",
    "notion", "sheets", "docs",
    # The assistant and the product.
    "mira", "metorite", "assistant",
})
#: The words right before a claim that say what one record holds: "The
#: invoice SHOWS no record of payment" is the state of the invoice, not a
#: lookup. A store of records is no such subject: "Our records show no
#: record of an order" and "The search results show no matches" stay claims.
_RECORD_STATE = re.compile(
    r"\b(?:orders?|deals?|leads?|contacts?|accounts?|invoices?|quot(?:e|es|ation|ations)"
    r"|payments?|e-?mails?|mails?|messages?|threads?|conversations?|tasks?|projects?"
    r"|tickets?|shipments?|notes?|files?|documents?|attachments?|statements?|history)"
    r"\s+(?:shows?|showed|has|had|holds?|held|contains?|lists?|says?|said|notes?"
    r"|mentions?)\s+$",
    re.I,
)
#: "does not exist YET": a record that the answer is about to make.
_YET = re.compile(r"\s+yet\b", re.I)
#: An act of the answer that made the record: "so I created it".
_MADE_IT = re.compile(
    rf"\b(?:so|and|then)\s+I(?:\s+have|[{_APOS}]ve)?\s+(?:created|made|added|set\s+up|opened"
    r"|logged)\b",
    re.I,
)
#: The filter of a list after a claim: "no matches FOR YOUR FILTER".
_LIST_FILTER = re.compile(
    r"\s*(?:(?:for|with|matching|that\s+match(?:es)?)\s+)?(?:your|the|this|these|those|that)"
    r"\s+(?:filters?|criteria|view)\b",
    re.I,
)
#: How far before a claim the reported-speech and record-state rules read.
_BEFORE_CHARS = 80
#: Words that start with a capital but name no record.
_NOT_NAMES = frozenset({
    "it", "this", "that", "these", "those", "he", "she", "they", "we", "you", "i",
    "there", "here", "which", "what", "who", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "today", "tomorrow", "yesterday",
})
#: Small words that may sit inside a name: "Bank of Baroda", "Neuropeak or Sharat".
_NAME_JOINS = frozenset({"of", "and", "or", "&", "de", "the"})
#: Code, and a line that quotes a block. Neither is prose of the answer.
_CODE = re.compile(r"```.*?```|`[^`\n]*`|^[ \t]*>.*$", re.S | re.M)
#: A quote inside a sentence. A record can hold the words "no such order",
#: and a claim that STARTS inside a quote is the record's text, not the
#: answer's. A name in a quote ("I could not find «Neuropeak»") stays. Each
#: quote is :data:`MAX_QUOTE_CHARS` long at most, so a scan never reads to
#: the end of the text from each position.
_QUOTE = re.compile(
    "|".join(
        f"{open_}[^{close}\\n]{{0,{MAX_QUOTE_CHARS}}}{close}"
        for open_, close in (("«", "»"), (_LDQUO, _RDQUO), (_LSQUO, _RSQUO), ('"', '"'))
    )
    + f"|(?<!\\w)'[^'\\n]{{0,{MAX_QUOTE_CHARS}}}'(?!\\w)"
)
#: The end of a sentence or of a line, for the claim check.
_CLAIM_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _is_name(span: str) -> bool:
    """True when *span* reads as the name of a record: a quote mark first, or
    a capital letter or a digit at the start of each word. A join word may
    sit inside ("Bank of Baroda"). A pronoun, a day and a month are no name."""
    words = [word.strip(_QUOTE_MARKS) for word in span.split()]
    raw = span.split()
    if not raw:
        return False
    if raw[0][:1] in _QUOTE_MARKS:
        return True
    if not words[0] or words[0].lower() in _NOT_NAMES:
        return False
    for i, word in enumerate(words):
        if 0 < i < len(words) - 1 and word.lower() in _NAME_JOINS:
            continue
        if not (word[:1].isupper() or word[:1].isdigit()):
            return False
    return True


def _is_person(word: str) -> bool:
    """True when *word*, the subject right before a verb of speech, names a
    person: a word of :data:`_PERSON_WORDS`, or a capital word that is no word
    of the tool, a store or the assistant (:data:`_SYSTEM_WORDS`)."""
    plain = word.strip(_QUOTE_MARKS).lower()
    if plain in _PERSON_WORDS:
        return True
    if plain in _SYSTEM_WORDS or plain in _NOT_NAMES:
        return False
    return word.strip(_QUOTE_MARKS)[:1].isupper()


def _rejected(sentence: str, match: re.Match[str], rule: str) -> bool:
    """True when a rule matched words that are no claim of a lookup: a stop
    word in the span, a reported speech, the state of a record, a record that
    the answer is about to make, a list filter, or a word that is no name.
    Each test reads at most :data:`_BEFORE_CHARS` before the match or a few
    words after it, so its work does not grow with the sentence.

    Reported speech counts only with a person as the subject right before
    the verb (:func:`_is_person`). The state of a record counts only with one
    record as the subject, never a store: "Our records show no record of an
    order" stays a claim."""
    at = match.start()
    groups = match.groupdict()
    span = groups.get("span") or ""
    if any(word.strip(_QUOTE_MARKS).lower() in _SPAN_STOP for word in span.split()):
        return True
    if groups.get("name") is not None and not _is_name(groups["name"]):
        return True
    subject = groups.get("subject")
    if subject is not None and subject[:1].isupper() and subject.lower() in _NOT_NAMES:
        return True
    before = sentence[max(0, at - _BEFORE_CHARS):at]
    reported = _REPORTED.search(before)
    if reported is not None and _is_person(reported.group("who")):
        return True
    if _RECORD_STATE.search(before):
        return True
    if rule == "does_not_exist" and _YET.match(sentence, match.end()):
        return True
    return _LIST_FILTER.match(sentence, match.end()) is not None


def _claim_in(sentence: str) -> str | None:
    """The rule code of the first claim of one *sentence*, or None.

    Each regex runs once over the sentence. The quote spans, the first hedge
    and the first act ("so I created it") are found once, and each match
    reads them by position. No test reads the sentence again for each match
    (FIND-7 fix round, P1)."""
    quoted = [m.span() for m in _QUOTE.finditer(sentence)]
    starts = [start for start, _end in quoted]
    hedge = _HEDGE.search(sentence)
    hedge_end = hedge.end() if hedge else len(sentence) + 1
    made = _MADE_IT.search(sentence)
    made_at = made.start() if made else -1
    for rule, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(sentence):
            at = match.start()
            i = bisect.bisect_right(starts, at) - 1
            if i >= 0 and at < quoted[i][1]:
                continue
            if hedge_end <= at:
                continue
            if rule == "does_not_exist" and made_at > at:
                # "The order did not exist, so I created it." Only this rule:
                # "I could not find the order, so I created it" stays a claim,
                # because the new record may be a second copy.
                continue
            if _rejected(sentence, match, rule):
                continue
            return rule
    return None


def not_found_claim(text: str) -> str | None:
    """The rule code of :data:`CLAIM_RULES` of the first sentence of *text*
    that says a record does not exist, or None (FIND-7, ``find_engine.md``
    §5.5).

    It reads the first :data:`MAX_CLAIM_CHARS` characters only, so its work
    is bounded, because it runs on the event loop. It reads English only.
    Pure.

    Each rule needs a record noun from a closed list, or a name. The tests
    hold each form that it reads as no claim, and only these: a question, a
    condition ("if", "unless", "whether"), a claim that starts inside a
    quote, a quoted block, code, a preposition or a list word between "find"
    and the noun ("any errors IN the invoice", "any OVERDUE tasks"), reported
    speech with a person as the subject right before the verb ("Priya
    replied that", "Ravi says he"), the state of one record right before the
    claim ("The invoice shows no record of"), a record that the answer makes
    ("does not exist yet", and "did not exist, so I created it" for the rule
    ``does_not_exist`` only) and a list filter ("no matches for your filter").

    It still fires on known false positives: empty lists in other words,
    reported speech and the state of a record in other forms, a capital word
    that names no record, a match that is no record, and an old fault that
    the answer reports. It misses "Sharath has no orders", "Our records show
    no order for Sharath", a name in lower case, a claim in a card, and a
    claim after the first :data:`MAX_CLAIM_CHARS` characters. It reads a
    quote of more than :data:`MAX_QUOTE_CHARS` characters as prose.
    ``find_engine.md`` §5.5 lists each case, and the tests keep each one.
    """
    plain = _CODE.sub(" ", str(text or "")[:MAX_CLAIM_CHARS])
    for sentence in _CLAIM_SENTENCE.split(plain):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        rule = _claim_in(sentence)
        if rule is not None:
            return rule
    return None


def holds_search_tool(names: Iterable[str]) -> bool:
    """True when *names* holds the tool that the claim check needs (FIND-7)."""
    return any(name == CLAIM_SEARCH_TOOL for name in names)


def holds_crm_tool(names: Iterable[str]) -> bool:
    """True when *names* holds a tool whose result arms the check."""
    return any(name in ARM_TOOLS for name in names)


def member_words(parent: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """The member's own words of a run, for :data:`MEMBER_MESSAGE`.

    A delegated run (its *parent* run context names a run) takes the
    parent's words. Its own payload is a message that the parent's model
    wrote, so it is never a source. Pass the context that the run READ
    before it set its own, because a run derives keys into its context
    early (H-236 ``no_egress``).
    """
    if parent.get("run_id"):
        return str(parent.get(MEMBER_MESSAGE) or "")
    words = payload.get("message") or payload.get("user_query") or ""
    return str(words)[:MEMBER_MESSAGE_CHARS]


# ── The text of a result, of the arguments, of a display call ──────────────

def _result_text(result: Any) -> str:
    """The text of a tool result, in any shape MAF hands a middleware."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return "\n".join(_result_text(item) for item in result)
    text = getattr(result, "text", None)
    if isinstance(text, str) and text:
        return text
    inner = getattr(result, "result", None)
    if inner is not None and inner is not result:
        return _result_text(inner)
    return str(result)


def _as_mapping(arguments: Any) -> Any:
    """The arguments of a call as plain data. A JSON string is parsed."""
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (ValueError, RecursionError):  # round 6: a deep text is text
            return arguments
    dump = getattr(arguments, "model_dump", None)
    if callable(dump):
        return dump()
    if isinstance(arguments, Mapping):
        return dict(arguments)
    return arguments


def _days_from(value: str, today: date) -> int | None:
    match = _ISO_DAY.match(value.strip())
    if not match:
        return None
    try:
        day = date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None
    return abs((today - day).days)


def argument_sources(arguments: Any, today: date | None = None) -> list[str]:
    """The numbers that the model chose as the arguments of a CRM call.

    "Top 5" after ``page_size=5`` and "the last 7 days" after
    ``updated_from=<a date 7 days ago>`` are true. So each value is a source,
    a date gives its count of days to *today*, and two dates give the days
    between them.
    """
    today = today or date.today()
    out: list[str] = []
    days: list[date] = []

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for value in node.values():
                _walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                _walk(value)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            out.append(str(node))
        elif isinstance(node, str):
            out.append(node)
            count = _days_from(node, today)
            if count is not None:
                out.append(str(count))
                match = _ISO_DAY.match(node.strip())
                if match:
                    days.append(date(int(match[1]), int(match[2]), int(match[3])))

    _walk(_as_mapping(arguments))
    for i, first in enumerate(days):
        for second in days[i + 1:]:
            out.append(str(abs((second - first).days)))
    return out


def _shown_string(text: str) -> str:
    """The text that a string of a spec shows. HTML loses its tags, and
    code shows nothing that the check can read."""
    if "<" in text and ">" in text:
        return _TAG.sub(" ", _SCRIPT.sub(" ", text))
    if _CODE_LIKE.search(text):
        return ""
    return text


def _shown_text(node: Any, key: str = "") -> list[str]:
    """Each shown string and figure of a card or report spec."""
    name = key.lower()
    if isinstance(node, Mapping):
        out: list[str] = []
        for k, value in node.items():
            out.extend(_shown_text(value, str(k)))
        return out
    if isinstance(node, (list, tuple)):
        return [part for value in node for part in _shown_text(value, key)]
    if isinstance(node, bool) or node is None:
        return []
    if isinstance(node, (int, float)):
        return [] if name in LAYOUT_KEYS else [str(node)]
    if isinstance(node, str):
        if name in HIDDEN_KEYS:
            return []
        stripped = node.strip()
        if stripped[:1] in "{[":
            try:
                return _shown_text(json.loads(stripped), key)
            except (ValueError, RecursionError):
                pass
        return [_shown_string(node)]
    return []


#: A choice word right before a figure in an option of ``ask_questions``:
#: "Last 90 days", "Top 10", "Above ₹10 lakh". The figure is a choice that
#: the member makes, not a claim, so the check skips it (fix round 5). The
#: ONE number check (``find_figures``) finds the figure. "Proposal (Rs 5.9
#: crore)" has no choice word, and its figure is checked.
_CHOICE_WORD = re.compile(
    r"\b(?i:top|bottom|last|past|next|first|above|below|over|under|at\s+least|"
    r"at\s+most|more\s+than|less\s+than|fewer\s+than|within|up\s+to)\s+$"
)
#: The keys of a question card that hold an option.
_OPTION_KEYS = ("label", "option", "options", "choice", "choices")


def _drop_choices(label: str) -> str:
    """*label* with each figure after a choice word taken out."""
    out = label
    for figure in reversed(find_figures(label)):
        if _CHOICE_WORD.search(label[:figure.start]):
            out = out[:figure.start] + out[figure.end:]
    return out


def _without_choices(node: Any, key: str = "") -> Any:
    """*node* with the choice figures of each option label taken out."""
    if isinstance(node, Mapping):
        return {k: _without_choices(v, str(k)) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_without_choices(v, key) for v in node]
    if isinstance(node, str):
        stripped = node.strip()
        if stripped[:1] in "{[":
            try:
                return _without_choices(json.loads(stripped), key)
            except (ValueError, RecursionError):
                pass
        if key.lower() in _OPTION_KEYS:
            return _drop_choices(node)
    return node


def display_text(name: str, arguments: Any) -> str:
    """The text that a display call shows the member, or "" (item 2).

    A spec nested too deep to walk gives its raw text, so the check still
    reads its numbers and never raises (fix round 6)."""
    try:
        return _display_text(name, arguments)
    except RecursionError:
        return arguments if isinstance(arguments, str) else ""


def _display_text(name: str, arguments: Any) -> str:
    if name not in DISPLAY_TOOLS:
        return ""
    data = _as_mapping(arguments)
    if name == "ask_questions":
        data = _without_choices(data)
    if name == "write_artifact":
        if not isinstance(data, Mapping):
            return ""
        path = str(data.get("path") or "").lower()
        content = data.get("content")
        if not path.endswith(TEXT_FILE_SUFFIXES) or not isinstance(content, str):
            return ""
        return _shown_string(content) if path.endswith((".html", ".htm")) else content
    return "\n".join(part for part in _shown_text(data) if part)


def _calls(response: ChatResponse) -> list[Any]:
    return [
        c for m in response.messages for c in (m.contents or ())
        if getattr(c, "type", "") == "function_call"
    ]


def _card_text(response: ChatResponse) -> str:
    return "\n".join(
        part for c in _calls(response)
        if (part := display_text(str(getattr(c, "name", "") or ""),
                                 getattr(c, "arguments", None)))
    )


# ── The ledger of ONE run ───────────────────────────────────────────────────

@dataclass
class RunLedger:
    """What one run has read, in memory, for that run only (item 4).

    ``armed`` turns true at the first CRM tool result. ``retried`` turns true
    when the run spends its one retry. ``results`` holds the newest results
    of :data:`FALLBACK_TOOLS`, for the fallback. ``book`` holds the numbers
    the answer may state. It grows as each tool result arrives (fix round
    3), so a check never parses the run's results again.
    """

    results: list[tuple[str, str]] = field(default_factory=list)
    book: Sources = field(default_factory=Sources)
    armed: bool = False
    retried: bool = False
    #: Round 8: each CRM read of the run in plain words, for "How I got
    #: these numbers". From the head line that the server wrote.
    reads: list[str] = field(default_factory=list)
    #: Round 8: each empty group of a grouped total, as (module, measure,
    #: group, share of the total in percent, value of the group).
    gaps: list[tuple[str, str, str, int, Decimal]] = field(default_factory=list)
    #: Round 8: a CRM read of the run holds a question that is still open.
    open_question: bool = False
    #: FIND-7: False when the number check is off for the run. Then the
    #: ledger reads no figure, and only the claim check reads it.
    numbers: bool = True
    #: FIND-7: True when the claim check is on for the run. Else the ledger
    #: counts no find result, as before FIND-7.
    claims: bool = False
    #: FIND-7: the results of :data:`FIND_TOOLS` (``finds``, which arm the
    #: hold), of :data:`CLAIM_ARM_TOOLS` (``lookups``, the field ``armed``)
    #: and of ``search_company``.
    finds: int = 0
    lookups: int = 0
    searches: int = 0
    #: FIND-7: a ``search_company`` answer of the run showed pass 2.
    second_pass: bool = False
    #: FIND-7: the claim check spent the retry of the run, or logged the one
    #: line of ``shadow``.
    claim_retried: bool = False
    claim_logged: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False,
                                  compare=False)

    def record(self, tool: str, text: str, *, arguments: Any = None,
               delegated: bool = False) -> None:
        """Keep one tool result. A CRM result arms the check.

        A delegated answer (*delegated*) or a result of
        :data:`MODEL_WRITTEN_TOOLS` is text that a model wrote, so it is no
        source. The arguments of a CRM call are sources. Only a tool
        result takes part in a derived figure (a sum, a difference or a
        percent of two), and ``acb_common.number_check`` caps the share of
        each result in that pool.

        FIND-7: with :attr:`claims`, a result of :data:`FIND_TOOLS` counts for
        the claim check first. With :attr:`numbers` False, nothing else
        happens.
        """
        if self.claims:
            self._note_find(tool, text)
        if not self.numbers:
            return
        if tool in FALLBACK_TOOLS:
            # One character more than the fallback shows, so it still
            # knows that a long result was cut.
            self.results.append((tool, text[:FALLBACK_CHARS + 1]))
            del self.results[:-FALLBACK_RESULTS]
        with self._lock:
            # A refusal ("Refused: ...") holds a status code and the route's
            # words, never a figure of the data, so it is no source.
            if not delegated and tool not in MODEL_WRITTEN_TOOLS and not is_refusal(text):
                self.book.add_text(text, derive=True)
            if tool in ARM_TOOLS:
                self.armed = True
                if arguments is not None:
                    for value in argument_sources(arguments):
                        self.book.add_text(value)
            self.book.settle()
        if tool in ARM_TOOLS and not is_refusal(text):
            self._note_read(tool, text, arguments)

    def _note_find(self, tool: str, text: str) -> None:
        """FIND-7: count a result of a find tool, and keep whether a
        ``search_company`` answer showed pass 2. It reads the lines that the
        tool's own code writes (``company_search_tools.ran_second_pass``)."""
        if tool not in FIND_TOOLS:
            return
        try:
            shown = tool == CLAIM_SEARCH_TOOL and ran_second_pass(text)
        except Exception as exc:  # the claim count never costs a number source
            _log.warning("answer_check.claim_record_failed", error=type(exc).__name__)
            shown = False
        with self._lock:
            self.finds += 1
            if tool in CLAIM_ARM_TOOLS:
                self.lookups += 1
            if tool == CLAIM_SEARCH_TOOL:
                self.searches += 1
                self.second_pass = self.second_pass or shown

    def _note_read(self, tool: str, text: str, arguments: Any) -> None:
        """Keep what the note of round 8 needs from one CRM read: its line
        of "How I got these numbers", its empty group, an open question."""
        if _OPEN_MARK.search(text):
            self.open_question = True
        if tool in HOW_TOOLS:
            line = read_line(text, arguments, tool)
            if line and line not in self.reads and len(self.reads) < MAX_READS:
                self.reads.append(line)
        gap = empty_group(text)
        if gap is not None:
            self.gaps.append(gap)

    def sources(self) -> Sources:
        """Each source of the run (item 4), the member's words included.

        The member's words of this check go on a layer over the run's book,
        so they are a source as they stand and change nothing in the book.
        """
        try:
            from acb_skills.write_artifact import artifact_context

            words = str(artifact_context().get(MEMBER_MESSAGE) or "")
        except Exception:  # no run context: no member words
            words = ""
        if not words:
            return self.book
        book = self.book.layer()
        book.add_text(words)
        return book

    def check(self, text: str) -> CheckResult:
        """Check *text* against the sources of the run."""
        return check(text, self.sources())


def retry_note(result: CheckResult) -> str:
    """The text of the one retry request. It names each failed number."""
    named = ", ".join(f'"{f.text}"' for f in result.unmatched[:MAX_NAMED])
    return (
        "A check of your answer found numbers that no tool result of this turn "
        f"holds: {named}. Write the answer again, with any card again. Copy "
        "each number from a tool result, or round it and keep its unit. You "
        "may state a number that you work out from TWO tool numbers: their sum, "
        "their difference, a percent share or change, or an average. Then you "
        "must state BOTH of those tool numbers in the same paragraph, next to "
        "the result, with a word such as total, net, gap, share, more or "
        "average. A percent in brackets right after a figure is its share. "
        "A total may come with its parts: \"27 (20 won, 7 lost)\". A ratio "
        "names both figures: \"₹13.53 crore is 6x ₹2.245 crore\". For a rank, "
        "a limit or a count of items, cite the figure as the tool gave it. For "
        "a share or a rate, state the whole and the part too. "
        "For anything more, call crm_aggregate with the right group or filter, "
        "or leave the number out. Write each figure in digits."
    )


#: What "How I got these numbers" calls a read that has no head line.
_READ_WORDS: dict[str, str] = {
    "crm_stage_dwell": "The days that deals stay in each stage",
    "get_pipeline": "The deal pipeline",
}
#: The words of a measure key in the head line of a total.
_MEASURE_WORDS = ((re.compile(r"\bsum_amount\b"), "sum of the amount"),
                  (re.compile(r"\bweighted\b(?! amount)"), "weighted amount"))


def _head(tool: str, text: str, arguments: Any) -> str | None:
    """The plain words of what one read counted, with no figure."""
    if tool in _READ_WORDS:
        return _READ_WORDS[tool]
    if tool == "crm_report":
        data = _as_mapping(arguments)
        kind = str(data.get("kind") or "").replace("-", " and ") if isinstance(
            data, Mapping) else ""
        return f"The {kind} report of deals".replace("  ", " ")
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines or lines[0][:1] in "-*":
        return None
    head = lines[0].split(": ", 1)[0].rstrip(":").strip()
    for pattern, words in _MEASURE_WORDS:
        head = pattern.sub(words, head)
    return head.replace("_", " ") or None


def read_line(text: str, arguments: Any = None, tool: str = "crm_aggregate") -> str | None:
    """One CRM read in plain words: the head line that the server wrote,
    without its figure, then its filters. "Deals, sum of Amount by
    Region. No filter." Each label comes from the server, never a key, and
    a key word of a measure ("sum_amount") reads as words."""
    head = _head(tool, text, arguments)
    if not head:
        return None
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    filters = next((line for line in lines if line.startswith("Filters:")), "")
    if not filters:
        words = _filter_words(arguments)
        filters = f"Filters: {words}." if words else "No filter."
    return f"{head}. {filters}"


def _filter_words(arguments: Any) -> str:
    data = _as_mapping(arguments)
    if not isinstance(data, Mapping):
        return ""
    parts = [f"{words} {data[key]}" for key, words in _FILTER_WORDS.items()
             if data.get(key) not in (None, "")]
    if data.get("query"):
        # SEM-1b-2: a total filtered by `query` names its leaves. No tool
        # sends `query` yet (follow-up row SEM-1b-2b), so this is dead code
        # in the chat until then.
        from acb_skills.crm_tools import query_filter_words

        words = query_filter_words(data.get("query"))
        if words:
            parts.append(words)
    return "; ".join(parts)


def empty_group(text: str) -> tuple[str, str, str, int, Decimal] | None:
    """The empty group of a grouped CRM total, when it holds at least
    :data:`EMPTY_SHARE` of the total: (module, measure, group, share in
    percent, value). "(none)" is the label of the server for a record with
    no value in the group field."""
    lines = (text or "").splitlines()
    head = _GROUPED_HEAD.match(lines[0].strip()) if lines else None
    if head is None:
        return None
    empty = total = None
    for line in lines[1:]:
        row = _LIST_ROW.match(line)
        if row and row.group("label").strip().lower() in EMPTY_LABELS:
            found = find_figures(row.group("value"))
            empty = abs(found[0].value) if found else empty
        elif line.strip().startswith("Total:"):
            found = find_figures(line.strip()[len("Total:"):])
            total = abs(found[0].value) if found else total
    if not empty or not total:
        return None
    share = float(empty / total)
    if share < EMPTY_SHARE:
        return None
    module, _, measure = head.group("what").partition(", ")
    return (module.strip(), measure.strip() or "total", head.group("group").strip(),
            round(share * 100), empty)


def _money_words(value: Any) -> str:
    """Rupees in the one Indian form of the CRM tools (``crm_tools``)."""
    from acb_skills.crm_tools import lakh_crore

    return lakh_crore(float(value))


def _near_words(figure: Figure, near: Any) -> str:
    if figure.kind == MONEY:
        return _money_words(near)
    whole = int(near)
    return group_indian(whole) if near == whole else f"{float(near):,.2f}"


def _named(figure: Figure, book: Sources) -> str:
    """One failed figure, with a reason in plain words and an action."""
    if figure.kind == PERCENT:
        return NOTE_PERCENT.format(figure=figure.text)
    near = book.nearest(figure)
    if near is not None:
        return NOTE_NEAR.format(figure=figure.text, near=_near_words(figure, near))
    return NOTE_MISSING.format(figure=figure.text)


#: The end of a sentence or of a line, for the question of an empty group.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")


def _sentence_of(text: str) -> Callable[[int], int]:
    ends = [m.start() for m in _SENTENCE_END.finditer(text)]
    return lambda at: bisect.bisect_left(ends, at)


def _question(ledger: RunLedger, result: CheckResult) -> str | None:
    """ONE question about an ambiguity that the check can see, or None. An
    open CRM question of the run comes first. Then the largest empty group
    of a grouped total whose value the answer states in the sentence (or
    the table row) of a failed figure."""
    if ledger.open_question:
        return QUESTION_OPEN
    # The answer must state the value of the empty group next to a failed
    # figure, so the question is about the failure, not a guess.
    sentence = _sentence_of(result.text)
    failed = {sentence(f.start) for f in result.unmatched}
    stated = [gap for gap in ledger.gaps
              if any(abs(abs(f.value) - gap[4]) <= f.room and sentence(f.start) in failed
                     for f in result.figures)]
    if not stated:
        return None
    module, measure, group, share, _value = max(stated, key=lambda gap: gap[3])
    return QUESTION_EMPTY.format(module=module, share=share, measure=measure, group=group)


def _how(ledger: RunLedger) -> str:
    if not ledger.reads:
        return HOW_NONE
    return "\n".join([HOW_HEAD, *(f"- {line}" for line in ledger.reads), HOW_TAIL])


def many_failed(failed: int, figures: int) -> bool:
    """True when *failed* of *figures* is a limit of the check, not of the
    answer: more than :data:`NAMED_MAX`, or more than :data:`MANY_SHARE` of
    the figures when :data:`MANY_MIN_FAILED` or more failed."""
    return failed > NAMED_MAX or (failed >= MANY_MIN_FAILED and failed > MANY_SHARE * figures)


def note_text(result: CheckResult, ledger: RunLedger) -> tuple[str, str]:
    """The kind of the note and its text, after an answer that still failed
    the check (round 8). The text starts with a blank line, or is empty.

    * A failed figure that is the sum or the difference of two figures of
      the answer gets one sentence that says so, and no warning.
    * More than :data:`NAMED_MAX` failed figures, or more than
      :data:`MANY_SHARE` of all figures: a limit of the check. The note
      names no figure, and says how the answer read the CRM.
    * An ambiguity that the check can see becomes ONE question.
    * Else each failed figure, :data:`NAMED_MAX` at most, gets a reason in
      plain words and an action.
    """
    failed: list[Figure] = []
    for figure in result.unmatched:
        if figure.text not in {f.text for f in failed}:
            failed.append(figure)
    if not failed:
        return NOTE_NONE, ""
    too_many = len(failed) > MAX_EXPLAIN
    explained: list[str] = []
    rest: list[Figure] = []
    for figure in failed:
        found = None if too_many else arithmetic(result, figure)
        if found is None:
            rest.append(figure)
        else:
            a, word, b = found
            explained.append(NOTE_SUM.format(figure=figure.text, a=a.text, word=word, b=b.text))
    many = many_failed(len(rest), len(result.figures))
    parts: list[str] = [] if many else explained[:NAMED_MAX]
    kind = NOTE_EXPLAINED if parts else NOTE_NONE
    if rest:
        question = _question(ledger, result)
        if question:
            parts.append(question)
            kind = NOTE_QUESTION
        if many:
            parts.append(_how(ledger))
            kind = kind if question else NOTE_HOW
        elif not question:
            book = ledger.sources()
            parts.append("\n".join(_named(f, book) for f in rest[:NAMED_MAX]))
            kind = NOTE_NAMED
    if not parts:
        return NOTE_NONE, ""
    return kind, "\n\n" + "\n\n".join(parts)


def fallback_text(ledger: RunLedger) -> str:
    """What the member reads when the answer failed the check (item 7)."""
    shown = [(t, x) for t, x in ledger.results if t in FALLBACK_TOOLS and x.strip()]
    shown = shown[-FALLBACK_RESULTS:]
    if not shown:
        return f"{FALLBACK_LEAD} {FALLBACK_NONE}"
    blocks = []
    for _tool, text in shown:
        body = text.strip()
        if len(body) > FALLBACK_CHARS:
            body = body[:FALLBACK_CHARS].rstrip() + "\n…"
        blocks.append(body)
    return f"{FALLBACK_LEAD} {FALLBACK_FIGURES}\n\n" + "\n\n".join(blocks)


# ── The two middlewares ─────────────────────────────────────────────────────

class RecordToolResults(FunctionMiddleware):
    """Keeps each tool result of ONE run, and the arguments of a CRM call."""

    def __init__(self, ledger: RunLedger) -> None:
        self._ledger = ledger

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        await call_next()
        function = getattr(context, "function", None)
        name = tool_name(function) if function is not None else ""
        try:
            text = _result_text(getattr(context, "result", None))
            record = functools.partial(
                self._ledger.record, name, text,
                arguments=getattr(context, "arguments", None),
                delegated=function is not None and is_delegation_tool(function),
            )
            if len(text) > RECORD_INLINE_CHARS:
                await asyncio.to_thread(record)
            else:
                record()
        except Exception as exc:  # the record never breaks a tool call
            _log.warning("answer_check.record_failed", tool=name, error=type(exc).__name__)


def _no_text(contents: Sequence[Any]) -> list[Any]:
    return [c for c in contents if getattr(c, "type", "") != "text"]


def _strip_updates(updates: Sequence[ChatResponseUpdate]) -> list[ChatResponseUpdate]:
    """*updates* with each text taken out. The tool calls and the rest stay."""
    out: list[ChatResponseUpdate] = []
    for update in updates:
        kept = _no_text(update.contents)
        if kept:
            out.append(ChatResponseUpdate(
                contents=kept, role=update.role, response_id=update.response_id,
                message_id=update.message_id, model=update.model,
                finish_reason=update.finish_reason,
            ))
    return out


#: The three answers of :meth:`CheckAnswers._verdict`.
PASS = "pass"
STRIP = "strip"
FAIL = "fail"


def _beat() -> None:
    """Put one :data:`HOLD_EVENT` on the run queue. Never raises."""
    executor = sys.modules.get("orchestrator.executor")
    if executor is None:  # no executor in this process: no run queue
        return
    try:
        from acb_skills.write_artifact import artifact_context

        queue = executor.resolve_run_queue(artifact_context().get("session_id"))
        if queue is not None:
            queue.put_nowait({"type": "CUSTOM", "name": HOLD_EVENT, "value": {}})
    except Exception:  # a sign of life never breaks a run
        pass


class CheckAnswers(ChatMiddleware):
    """Checks each model response of ONE armed run (items 2, 6, 7 and 10)."""

    def __init__(self, ledger: RunLedger, mode: str) -> None:
        self._ledger = ledger
        self._mode = mode
        self._last_beat = 0.0

    def _safe_verdict(self, response: ChatResponse) -> tuple[str, CheckResult | None]:
        """:meth:`_verdict`, but a check that raises gives ``pass`` and no
        result: the answer goes out unchanged, and the log says why. A
        failure of the check must never lose the member's turn (round 6)."""
        try:
            return self._verdict(response)
        except Exception as exc:
            _log.warning("answer_check.check_failed", mode=self._mode,
                         error=type(exc).__name__)
            return PASS, None

    def _log(self, result: CheckResult | None, outcome: str, note: str | None = None) -> None:
        # The run id comes from the bound run context (``acb_common._log``).
        # ``note`` is the KIND of the note of round 8, never its text.
        if result is None:
            return
        extra = {"note": note} if note is not None else {}
        _log.info(
            "answer_check.checked", mode=self._mode,
            numbers=len(result.figures), mismatches=len(result.unmatched),
            outcome=outcome, **extra,
        )

    def _note(self, result: CheckResult) -> tuple[str, str]:
        """:func:`note_text`, but a note that raises gives no note: the
        answer still goes out (round 6)."""
        try:
            return note_text(result, self._ledger)
        except Exception as exc:
            _log.warning("answer_check.note_failed", error=type(exc).__name__)
            return NOTE_NONE, ""

    def _verdict(self, response: ChatResponse) -> tuple[str, CheckResult]:
        """``pass``, ``strip`` (the text failed, the calls may go) or ``fail``.

        A display call shows its arguments to the member, so a failed card
        fails the whole answer, and never ships. ``strip`` is only for a
        step whose calls are ALL in :data:`QUIET_TOOLS`. Any other call
        makes a failed step fail (fix round 4).
        """
        book = self._ledger.sources()
        card = _card_text(response)
        result = check("\n".join(p for p in (response.text, card) if p), book)
        if result.ok:
            return PASS, result
        calls = _calls(response)
        quiet = all(str(getattr(c, "name", "") or "") in QUIET_TOOLS for c in calls)
        return (STRIP if calls and quiet else FAIL), result

    async def _drain(self, stream: ResponseStream) -> tuple[list[ChatResponseUpdate], ChatResponse]:
        """Read the whole of *stream*, with a sign of life as it goes."""
        updates: list[ChatResponseUpdate] = []
        async for update in stream:
            updates.append(update)
            now = time.monotonic()
            if now - self._last_beat >= HEARTBEAT_S:
                self._last_beat = now
                _beat()
        return updates, await stream.get_final_response()

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        if not self._ledger.armed:
            await call_next()
            return
        if self._mode == SHADOW:
            await call_next()
            self._shadow(context)
            return
        await call_next()
        if isinstance(context.result, ResponseStream):
            # Take the inner stream now: the next line puts the held one in
            # its place, and the held one reads the inner one.
            context.result = ResponseStream(
                self._held(context, call_next, context.result),
                finalizer=ChatResponse.from_updates,
            )
        elif isinstance(context.result, ChatResponse):
            context.result = await self._checked(context, call_next, context.result)

    # shadow: check at the end, change nothing

    def _shadow(self, context: Any) -> None:
        def _look(response: ChatResponse) -> ChatResponse:
            try:
                verdict, result = self._verdict(response)
                if verdict == PASS:
                    self._log(result, "shadow_pass")
                else:
                    # The kind of note that ``on`` would add, for the replay.
                    self._log(result, "shadow_mismatch", self._note(result)[0])
            except Exception as exc:
                _log.warning("answer_check.shadow_failed", error=type(exc).__name__)
            return response

        if isinstance(context.result, ResponseStream):
            context.result.with_result_hook(_look)
        elif isinstance(context.result, ChatResponse):
            _look(context.result)

    # on: hold, check, retry once, then the fallback

    def _retry_messages(self, messages: list[Any], response: ChatResponse,
                        result: CheckResult) -> list[Any]:
        draft = response.text or "I drew a card with numbers."
        return [*messages, Message("assistant", [draft]),
                Message("user", [retry_note(result)])]

    async def _held(
        self, context: Any, call_next: Callable[[], Any], stream: ResponseStream,
    ) -> AsyncIterator[ChatResponseUpdate]:
        updates, response = await self._drain(stream)
        messages = list(context.messages)
        verdict, result = self._safe_verdict(response)
        if verdict == PASS or result is None:
            self._log(result, "pass")
            for update in updates:
                yield update
            return
        if verdict == STRIP:
            self._log(result, "step_text_removed")
            for update in _strip_updates(updates):
                yield update
            return
        if not self._ledger.retried:
            self._ledger.retried = True
            self._log(result, "retry")
            context.messages = self._retry_messages(messages, response, result)
            await call_next()
            retry_updates, second = await self._drain(context.result)
            again, result2 = self._safe_verdict(second)
            if again != FAIL or result2 is None:
                self._log(result2, "retry_pass" if again == PASS else "retry_call")
                kept = retry_updates if again == PASS else _strip_updates(retry_updates)
                for update in kept:
                    yield update
                return
            result, response, updates = result2, second, retry_updates
        last = updates[-1] if updates else None
        if (response.text or "").strip():
            # Round 7: the answer goes out, and it is never replaced. Round
            # 8: the note explains, asks or says how, in plain words.
            kind, note = self._note(result)
            self._log(result, "caveat", kind)
            for update in updates:
                yield update
            if not note:
                return
            yield ChatResponseUpdate(
                contents=[Content.from_text(note)], role="assistant",
                response_id=getattr(last, "response_id", None) or response.response_id,
                message_id=getattr(last, "message_id", None),
                model=getattr(last, "model", None), finish_reason="stop",
            )
            return
        self._log(result, "fallback")
        yield ChatResponseUpdate(
            contents=[Content.from_text(fallback_text(self._ledger))],
            role="assistant",
            response_id=getattr(last, "response_id", None) or response.response_id,
            message_id=getattr(last, "message_id", None),
            model=getattr(last, "model", None),
            finish_reason="stop",
        )

    async def _checked(
        self, context: Any, call_next: Callable[[], Any], response: ChatResponse,
    ) -> ChatResponse:
        messages = list(context.messages)
        verdict, result = self._safe_verdict(response)
        if verdict == PASS or result is None:
            self._log(result, "pass")
            return response
        if verdict == STRIP:
            self._log(result, "step_text_removed")
            return _without_text(response)
        if not self._ledger.retried:
            self._ledger.retried = True
            self._log(result, "retry")
            context.messages = self._retry_messages(messages, response, result)
            await call_next()
            second = context.result
            again, result2 = self._safe_verdict(second)
            if again == PASS or result2 is None:
                self._log(result2, "retry_pass")
                return second
            if again == STRIP:
                self._log(result2, "retry_call")
                return _without_text(second)
            result, response = result2, second
        if (response.text or "").strip():
            kind, note = self._note(result)
            self._log(result, "caveat", kind)
            return _with_caveat(response, note) if note else response
        self._log(result, "fallback")
        return ChatResponse(
            messages=[Message("assistant", [fallback_text(self._ledger)])],
            response_id=response.response_id, model=response.model,
            finish_reason="stop",
        )


class CheckClaims(ChatMiddleware):
    """The "not found" check of ONE run (WS-FIND FIND-7, ``find_engine.md`` §5.5).

    It sits INSIDE :class:`CheckAnswers`, so it reads the model's own text,
    before the number check adds a note. Both share the ledger, so the run
    has ONE retry in all (``RunLedger.retried``).

    It judges only a final answer: a response with no tool call. A step that
    calls a tool is no answer, because the run goes on.
    """

    def __init__(self, ledger: RunLedger, mode: str) -> None:
        self._ledger = ledger
        self._mode = mode
        self._last_beat = 0.0

    #: The one drain of a held stream, with its sign of life.
    _drain = CheckAnswers._drain

    def _claim(self, response: ChatResponse) -> str | None:
        """The rule of a claim of a final answer, or None. A check that
        raises gives None, so the answer goes out unchanged."""
        try:
            if _calls(response):
                return None
            return not_found_claim(response.text or "")
        except Exception as exc:
            _log.warning("answer_check.claim_failed", mode=self._mode,
                         error=type(exc).__name__)
            return None

    def _log(self, outcome: str, rule: str) -> None:
        # The run id comes from the bound run context (``acb_common._log``).
        # The line holds codes, counts and one bool, never a word of the
        # answer (:data:`CLAIM_LOG_FIELDS`). ``armed``: a result of
        # :data:`CLAIM_ARM_TOOLS` came before the answer in this run. False
        # means that only an app's search tool ran, or no find tool at all.
        _log.info(
            "answer_check.claim", mode=self._mode, outcome=outcome, rule=rule,
            searches=self._ledger.searches, finds=self._ledger.finds,
            armed=self._ledger.lookups > 0,
        )

    def _miss(self, response: ChatResponse) -> str | None:
        """The rule of a claim that has no pass 2 in the run, or None. A
        claim WITH pass 2 logs ``pass`` in ``on``."""
        rule = self._claim(response)
        if rule is None:
            return None
        if self._ledger.second_pass:
            if self._mode == ON:
                self._log("pass", rule)
            return None
        return rule

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        if self._mode == SHADOW:
            await call_next()
            self._watch(context, unheld=False)
            return
        if not self._ledger.finds:
            # No find tool has answered in this run yet, so the answer is not
            # held: the member reads it as it streams, and the check logs a
            # claim as `unheld_miss`. After any find result, a claim with no
            # pass 2 is held, an app's search tool alone too (round 5).
            await call_next()
            self._watch(context, unheld=True)
            return
        await call_next()
        if isinstance(context.result, ResponseStream):
            context.result = ResponseStream(
                self._held(context, call_next, context.result),
                finalizer=ChatResponse.from_updates,
            )
        elif isinstance(context.result, ChatResponse):
            context.result = await self._checked(context, call_next, context.result)

    # shadow, and an unheld answer of on: look at the end, change nothing

    def _watch(self, context: Any, *, unheld: bool) -> None:
        def _look(response: ChatResponse) -> ChatResponse:
            rule = self._claim(response)
            if rule is None or self._ledger.claim_logged:
                return response
            if unheld:
                if not self._ledger.second_pass:
                    self._log("unheld_miss", rule)
                return response
            self._ledger.claim_logged = True
            self._log("shadow_pass" if self._ledger.second_pass else "shadow_miss", rule)
            return response

        if isinstance(context.result, ResponseStream):
            context.result.with_result_hook(_look)
        elif isinstance(context.result, ChatResponse):
            _look(context.result)

    # on: hold, check, one more pass, then the answer as it is

    def _retry_messages(self, messages: list[Any], response: ChatResponse) -> list[Any]:
        return [*messages, Message("assistant", [response.text or ""]),
                Message("user", [CLAIM_RETRY_NOTE])]

    def _spend(self, rule: str) -> bool:
        """True when this claim takes the one retry of the run. A retry that
        is spent logs why the answer goes as it is."""
        if not self._ledger.retried:
            self._ledger.retried = True
            self._ledger.claim_retried = True
            self._log("retry", rule)
            return True
        self._log("after_retry_miss" if self._ledger.claim_retried else "retry_spent", rule)
        return False

    def _after(self, response: ChatResponse) -> None:
        """The answer of the one more pass goes as it is (D-FIND-8). Log a
        claim that it still makes."""
        rule = self._miss(response)
        if rule is not None:
            self._log("after_retry_miss", rule)

    async def _held(
        self, context: Any, call_next: Callable[[], Any], stream: ResponseStream,
    ) -> AsyncIterator[ChatResponseUpdate]:
        updates, response = await self._drain(stream)
        messages = list(context.messages)
        rule = self._miss(response)
        if rule is None or not self._spend(rule):
            for update in updates:
                yield update
            return
        context.messages = self._retry_messages(messages, response)
        try:
            await call_next()
            retry_updates, second = await self._drain(context.result)
        except Exception as exc:
            # The one more pass failed. The held draft goes out as it is, as
            # with the check off. A cancel is no Exception, so it goes on up.
            self._failed(context, messages, rule, exc)
            for update in updates:
                yield update
            return
        self._after(second)
        for update in retry_updates:
            yield update

    async def _checked(
        self, context: Any, call_next: Callable[[], Any], response: ChatResponse,
    ) -> ChatResponse:
        messages = list(context.messages)
        rule = self._miss(response)
        if rule is None or not self._spend(rule):
            return response
        context.messages = self._retry_messages(messages, response)
        try:
            await call_next()
        except Exception as exc:
            self._failed(context, messages, rule, exc)
            return response
        second = context.result
        self._after(second)
        return second

    def _failed(self, context: Any, messages: list[Any], rule: str, exc: Exception) -> None:
        """The one more pass raised: put the messages back and log
        ``retry_failed``. The error type goes to a warning line, never its text."""
        context.messages = messages
        self._log("retry_failed", rule)
        _log.warning("answer_check.claim_retry_failed", error=type(exc).__name__)


def _role(message: Any) -> str:
    role = getattr(message, "role", "")
    return str(getattr(role, "value", role) or "")


def _with_caveat(response: ChatResponse, caveat: str) -> ChatResponse:
    """*response* with *caveat* after the text of its last message that has
    text. Its tool calls and the rest stay (fix round 7)."""
    messages = list(response.messages)
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        contents = list(m.contents or ())
        last = next((j for j in range(len(contents) - 1, -1, -1)
                     if getattr(contents[j], "type", "") == "text"), None)
        if last is None:
            continue
        # The caveat joins the last text itself, so no space comes between.
        contents[last] = Content.from_text((contents[last].text or "") + caveat)
        messages[i] = Message(
            _role(m) or "assistant", contents,
            author_name=getattr(m, "author_name", None),
            message_id=getattr(m, "message_id", None),
        )
        break
    return ChatResponse(
        messages=messages, response_id=response.response_id, model=response.model,
        finish_reason=response.finish_reason, usage_details=response.usage_details,
    )


def _without_text(response: ChatResponse) -> ChatResponse:
    """*response* with its text taken out and its tool calls kept."""
    messages = [
        Message(
            _role(m) or "assistant", _no_text(m.contents or ()),
            author_name=getattr(m, "author_name", None),
            message_id=getattr(m, "message_id", None),
        )
        for m in response.messages
    ]
    return ChatResponse(
        messages=messages, response_id=response.response_id,
        model=response.model, finish_reason=response.finish_reason,
        usage_details=response.usage_details,
    )


class AnswerCheckProvider(ContextProvider):
    """Adds the two middlewares above to ONE run of a native MAF agent.

    ``_tool_injection._inject_agent_tools`` attaches it to the run's own view
    of the agent (``agent_with_providers``), never to a shared agent object.
    Each ``run`` call gets a new ledger, so nothing carries from one run to
    the next.

    *mode* is the mode of the number check, and *claim_mode* the mode of the
    "not found" check (FIND-7). A check with the mode ``off`` adds no
    middleware. :class:`CheckClaims` goes after :class:`CheckAnswers`, so it
    is the inner one.
    """

    def __init__(self, mode: str, claim_mode: str = OFF) -> None:
        super().__init__(SOURCE_ID)
        self.mode = mode
        self.claim_mode = claim_mode
        self.ledger = self._ledger()

    def _ledger(self) -> RunLedger:
        return RunLedger(numbers=self.mode != OFF, claims=self.claim_mode != OFF)

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        self.ledger = self._ledger()
        chat: list[Any] = []
        if self.mode != OFF:
            chat.append(CheckAnswers(self.ledger, self.mode))
        if self.claim_mode != OFF:
            chat.append(CheckClaims(self.ledger, self.claim_mode))
        context.extend_middleware(SOURCE_ID, [*chat, RecordToolResults(self.ledger)])


__all__ = [
    "ARM_TOOLS",
    "CLAIM_ARM_TOOLS",
    "CLAIM_LOG_FIELDS",
    "CLAIM_OUTCOMES",
    "CLAIM_RETRY_NOTE",
    "CLAIM_RULES",
    "CLAIM_SEARCH_TOOL",
    "CRM_ASSISTANT_READS",
    "DISPLAY_TOOLS",
    "FALLBACK_TOOLS",
    "FIND_TOOLS",
    "HEARTBEAT_S",
    "HOLD_EVENT",
    "MAX_CLAIM_CHARS",
    "MEMBER_MESSAGE",
    "MODEL_WRITTEN_TOOLS",
    "MODES",
    "OFF",
    "ON",
    "QUIET_TOOLS",
    "RECALL_TOOLS",
    "SHADOW",
    "AnswerCheckProvider",
    "CheckAnswers",
    "CheckClaims",
    "RecordToolResults",
    "RunLedger",
    "answer_check_mode",
    "argument_sources",
    "display_text",
    "empty_group",
    "fallback_text",
    "holds_crm_tool",
    "holds_search_tool",
    "many_failed",
    "member_words",
    "not_found_claim",
    "note_text",
    "read_line",
    "retry_note",
]
