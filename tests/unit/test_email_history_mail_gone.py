"""WS-17 task 1987: the server says which SKIPPED run is a gone-mail skip.

The History rows and the timeline events carry `mail_gone`. The UI never guesses
it from `rule_id`, because a suppressed skip also keeps its rule_id.
"""
from __future__ import annotations

import inspect
import json

from gateway.routes.email.automation import runner
from gateway.routes.email.automation.runner import is_mail_gone_skip
from gateway.routes.email.provider_errors import MESSAGE_GONE_DETAIL

GONE = {"type": "LABEL", "error": MESSAGE_GONE_DETAIL}
OTHER = {"type": "ARCHIVE", "error": "Mailbox quota exceeded"}


def test_skipped_with_all_gone_errors_is_true() -> None:
    assert is_mail_gone_skip("SKIPPED", [GONE]) is True
    assert is_mail_gone_skip("SKIPPED", [GONE, dict(GONE, type="ARCHIVE")]) is True


def test_errors_as_a_json_string_are_read() -> None:
    assert is_mail_gone_skip("SKIPPED", json.dumps([GONE])) is True
    assert is_mail_gone_skip("SKIPPED", "not json") is False


def test_suppressed_row_is_false() -> None:
    # SKIPPED, rule_id kept, "Suppressed: ..." reason, no errors.
    assert is_mail_gone_skip("SKIPPED", []) is False
    assert is_mail_gone_skip("SKIPPED", None) is False


def test_plain_no_match_is_false() -> None:
    assert is_mail_gone_skip("SKIPPED", None) is False


def test_failed_with_gone_errors_is_false() -> None:
    assert is_mail_gone_skip("FAILED", [GONE]) is False


def test_one_gone_and_one_other_error_is_false() -> None:
    assert is_mail_gone_skip("SKIPPED", [GONE, OTHER]) is False


def test_both_payloads_emit_mail_gone() -> None:
    src = inspect.getsource(runner)
    assert src.count('"mail_gone": is_mail_gone_skip(') == 2
