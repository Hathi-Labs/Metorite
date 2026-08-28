"""The apply must leave `.venv` owned by the app user.

🔴 **This is the defect that hid every other one.** From 2026-08-26 to
2026-08-29 every push-path deploy failed, and the reported symptom was
"App still unreachable after 3 deploy+verify rounds" — a health fault, or a
network fault, which is where three days of diagnosis went.

The real failure was eight lines into `==> Syncing Python deps`:

    error: failed to remove file `…/sherpa_onnx-1.13.6.dist-info/INSTALLER`:
           Permission denied (os error 13)

`vps_apply.sh` runs under `set -e`, so that took the remaining ~480 lines with
it — the workbench build, the Operator Console block, the Caddy reload, all of
it. The checkout had already happened by then, so `git log` on the box read
CURRENT while every compiled surface stayed days behind. Both delivery paths
then agreed there was nothing to do: the pull path compares SHAs, and the SHA
was right.

The cause is two delivery paths with two UIDs over one venv:

    pull path   acb-pull.service runs `User=root`  → writes root-owned files
    push path   deploy.yml SSHes as the app user   → cannot remove them

Measured on the box 2026-08-29: 36 files of 9883 under `.venv` were `root:root`
in an otherwise `acb:acb` tree.

⚠️ The asymmetry is the whole thing. **The root path never fails** — root can
remove anything. It only leaves the mess that makes the NEXT app-user deploy
fail. So the fix belongs on the root side, after the sync, and a test that only
checked "a chown exists" would not catch a chown placed before it.

⚠️ Idiom inherited from `test_operator_console_deploy_wiring` and
`test_console_ladder_deploy_wiring`: **every assertion reads NON-COMMENT
lines.** The block under test carries a long comment naming every string here,
and a guard satisfied by prose certifies the documentation rather than the
wiring.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _venv_chown_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if "chown" in ln and ".venv" in ln]


def test_the_apply_normalises_venv_ownership() -> None:
    """Without this the venv accumulates root-owned files forever, and every
    app-user deploy after the first root run dies on the first one it meets."""
    lines = _executable_lines(_APPLY)
    assert _venv_chown_lines(lines), (
        "vps_apply.sh must chown $APP_DIR/.venv — otherwise a root-run apply "
        "leaves files the app-user path cannot remove, and `uv sync` aborts "
        "the whole deploy under set -e"
    )


def test_the_chown_happens_AFTER_uv_sync() -> None:
    """🔴 Ordering IS the fix, and it is the half a lazy test would miss.

    A chown before `uv sync` normalises the damage the LAST run left. It does
    nothing about the damage THIS run is about to create, so the next app-user
    deploy fails exactly as before. The repair has to follow the thing that
    causes the harm.
    """
    lines = _executable_lines(_APPLY)
    sync_at = next(i for i, ln in enumerate(lines) if ln.strip() == "uv sync")
    chown_at = next(i for i, ln in enumerate(lines) if ln in _venv_chown_lines(lines))
    assert chown_at > sync_at, (
        "chown the venv AFTER `uv sync`. Before it, the apply cleans up the "
        "previous run's mess and then makes an identical one"
    )


def test_it_only_chowns_when_running_as_root() -> None:
    """The app-user path must not attempt it. It would fail on the very files
    that are the problem, and under `set -e` that turns a deploy which merely
    needed repairing into one that cannot start."""
    lines = _executable_lines(_APPLY)
    joined = "\n".join(lines)
    assert "id -u" in joined, (
        "guard the chown on being root — the app user cannot chown root's "
        "files and would abort the deploy trying"
    )


def test_the_owner_is_DERIVED_from_APP_DIR_not_hardcoded() -> None:
    """⚠️ `acb` is this box's app user, not the contract.

    `APP_DIR` is already overridable at line 43, so a hardcoded `acb:acb` would
    silently chown to a user that does not exist on any box that installed
    elsewhere — and `chown` failing under `set -e` fails the deploy.
    """
    lines = _executable_lines(_APPLY)
    chowns = _venv_chown_lines(lines)
    assert chowns, "no venv chown to inspect"
    assert any("stat" in ln and "APP_DIR" in ln for ln in chowns), (
        "derive the owner from $APP_DIR with stat, so the apply stays correct "
        "on a box that installs somewhere other than /opt/acb/app"
    )
    assert not any("acb:acb" in ln for ln in chowns), (
        "do not hardcode the app user — APP_DIR is overridable, so the owner "
        "must be read from the tree the deploy is actually updating"
    )
