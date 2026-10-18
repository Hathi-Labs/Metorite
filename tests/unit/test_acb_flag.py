"""Fence for deploy/hostinger/acb-flag, the flag tool for the agent sessions (H-358).

Each test drives the real script on a scratch tree. ACB_FLAG_ROOT points it at
the tree and ACB_FLAG_DRY prints each systemctl call instead of running it. The
script honours both only for a non-root caller, so `sudo` cannot use them.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

# On Windows, `bash` from Python resolves to WSL's, which cannot see a C:/
# path. The tool runs on the Linux boxes, and CI runs this file on Linux.
pytestmark = pytest.mark.skipif(os.name == "nt", reason="the box tools run on Linux; CI runs this")

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TOOL = _ROOT / "deploy/hostinger/acb-flag"
_REFRESH = _ROOT / "scripts/staging_refresh.sh"
_BOOT = _ROOT / "deploy/hostinger/bootstrap_staging.sh"


def _box(
    tmp: pathlib.Path, env: str | None = "staging", root_env: str = "", wb_env: str = ""
) -> pathlib.Path:
    app = tmp / "opt/acb/app"
    (app / "workbench/control_plane").mkdir(parents=True)
    head = f"DEPLOY_ENV={env}\n" if env else ""
    (app / ".env").write_text(
        f"{head}DATABASE_URL=postgres://u:secret@h/db\n{root_env}", encoding="utf-8"
    )
    (app / "workbench/control_plane/.env.local").write_text(
        f"AUTH_SECRET=wb-secret\n{wb_env}", encoding="utf-8"
    )
    (app / ".env").chmod(0o600)
    return app


def _run(tmp: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ACB_FLAG_ROOT": tmp.as_posix(), "ACB_FLAG_DRY": "1", "SUDO_USER": "dev"}
    return subprocess.run(
        ["bash", _TOOL.as_posix(), *args], capture_output=True, text=True, timeout=30, env=env
    )


def _lines(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _wb(app: pathlib.Path) -> list[str]:
    return _lines(app / "workbench/control_plane/.env.local")


def test_staging_set_writes_the_file_the_overlay_and_the_log(tmp_path: pathlib.Path) -> None:
    app = _box(tmp_path, root_env="KNOWLEDGE_ENABLED=false\n")
    res = _run(tmp_path, "set", "KNOWLEDGE_ENABLED=true", "KNOWLEDGE_ORGS=*")
    assert res.returncode == 0, res.stderr
    assert "KNOWLEDGE_ENABLED=true" in _lines(app / ".env")
    assert "DATABASE_URL=postgres://u:secret@h/db" in _lines(app / ".env")
    assert (app / ".env").stat().st_mode & 0o777 == 0o600
    # KNOWLEDGE_ORGS was in neither file, so it goes to both: the tool cannot
    # know whether the gateway or the workbench reads it.
    assert "KNOWLEDGE_ORGS=*" in _lines(app / ".env")
    assert "KNOWLEDGE_ORGS=*" in _wb(app)
    assert sorted(_lines(tmp_path / "etc/acb/staging-flags.env")) == [
        "root:KNOWLEDGE_ENABLED=true",
        "root:KNOWLEDGE_ORGS=*",
        "workbench:KNOWLEDGE_ORGS=*",
    ]
    assert "DRY: systemctl restart acb-gateway" in res.stdout
    assert "DRY: systemctl restart acb-workbench" in res.stdout
    log = (tmp_path / "var/log/acb-flag.log").read_text(encoding="utf-8")
    assert "user=dev box=staging set root KNOWLEDGE_ENABLED old=false new=true" in log
    assert list(app.glob(".env.bak-acb-flag-*")), "no backup was made"


def test_a_next_public_flag_goes_to_the_workbench_and_starts_a_rebuild(
    tmp_path: pathlib.Path,
) -> None:
    app = _box(tmp_path)
    res = _run(tmp_path, "set", "NEXT_PUBLIC_SHELL_BAR=1")
    assert res.returncode == 0, res.stderr
    assert "NEXT_PUBLIC_SHELL_BAR=1" in _wb(app)
    assert "acb-flag-reapply.sh" in res.stdout
    assert "--slice=acb-staging.slice" in res.stdout  # the build stays under staging's cap
    assert "restart acb-gateway" not in res.stdout


def test_a_switch_only_the_workbench_holds_stays_there(tmp_path: pathlib.Path) -> None:
    app = _box(tmp_path, wb_env="ACCOUNT_SWITCHER_ENABLED=false\n")
    res = _run(tmp_path, "set", "ACCOUNT_SWITCHER_ENABLED=true")
    assert res.returncode == 0, res.stderr
    assert "ACCOUNT_SWITCHER_ENABLED=true" in _wb(app)
    assert not any(line.startswith("ACCOUNT_SWITCHER") for line in _lines(app / ".env"))
    assert "DRY: systemctl restart acb-workbench" in res.stdout
    assert "restart acb-gateway" not in res.stdout


def test_a_next_public_name_in_the_root_env_reaches_the_workbench_too(
    tmp_path: pathlib.Path,
) -> None:
    app = _box(tmp_path, root_env="NEXT_PUBLIC_PROJECTS_CHAT=0\n")
    res = _run(tmp_path, "set", "NEXT_PUBLIC_PROJECTS_CHAT=1")
    assert res.returncode == 0, res.stderr
    assert "NEXT_PUBLIC_PROJECTS_CHAT=1" in _lines(app / ".env")
    assert "NEXT_PUBLIC_PROJECTS_CHAT=1" in _wb(app)
    assert "acb-flag-reapply.sh" in res.stdout


@pytest.mark.parametrize(
    "arg",
    [
        "DATABASE_URL=x",
        "OPENAI_API_KEY_ENABLED=true",
        "AUTH_SECRET=x",
        "DEPLOY_ENV=production",
        "EMAIL_SYNC_ENABLED=true",
        "REACH_ENABLED=true",
        "WHATSAPP_ASSISTANT_ENABLED=true",
        "SELF_SERVE_SIGNUP_ENABLED=true",
        "RANDOM_THING=1",
        "KNOWLEDGE_ENABLED=a b",
        "KNOWLEDGE_ENABLED=$(id)",
    ],
)
def test_a_secret_an_outbound_switch_or_an_odd_value_is_refused(
    tmp_path: pathlib.Path, arg: str
) -> None:
    app = _box(tmp_path)
    before = (app / ".env").read_text(encoding="utf-8")
    res = _run(tmp_path, "set", arg)
    assert res.returncode == 2, res.stdout
    assert (app / ".env").read_text(encoding="utf-8") == before


def test_list_prints_switches_only(tmp_path: pathlib.Path) -> None:
    _box(
        tmp_path,
        root_env="KNOWLEDGE_ENABLED=true\nSOME_TOKEN=abc\nKNOWLEDGE_API_KEY=abc\n",
        wb_env="NEXT_PUBLIC_X=1\n",
    )
    res = _run(tmp_path, "list")
    assert res.returncode == 0, res.stderr
    assert "KNOWLEDGE_ENABLED=true" in res.stdout
    assert "NEXT_PUBLIC_X=1" in res.stdout
    # KNOWLEDGE_API_KEY is switch-shaped, so only the secret filter keeps it out.
    for secret in ("secret", "abc", "DATABASE_URL", "AUTH_SECRET", "KNOWLEDGE_API_KEY"):
        assert secret not in res.stdout


def test_unset_removes_the_line_and_records_the_removal(tmp_path: pathlib.Path) -> None:
    """The removal record keeps the flag off through the nightly refresh."""
    app = _box(tmp_path)
    assert _run(tmp_path, "set", "TASKS_LENS=on").returncode == 0
    res = _run(tmp_path, "unset", "TASKS_LENS")
    assert res.returncode == 0, res.stderr
    assert not any(line.startswith("TASKS_LENS=") for line in _lines(app / ".env"))
    assert sorted(_lines(tmp_path / "etc/acb/staging-flags.env")) == [
        "root:-TASKS_LENS",
        "workbench:-TASKS_LENS",
    ]


def test_reset_drops_the_record(tmp_path: pathlib.Path) -> None:
    _box(tmp_path)
    assert _run(tmp_path, "set", "TASKS_LENS=on").returncode == 0
    res = _run(tmp_path, "reset", "TASKS_LENS")
    assert res.returncode == 0, res.stderr
    assert _lines(tmp_path / "etc/acb/staging-flags.env") == []


def test_production_needs_the_flag_and_a_reason(tmp_path: pathlib.Path) -> None:
    app = _box(tmp_path, env="production")
    assert _run(tmp_path, "set", "KNOWLEDGE_ENABLED=true").returncode == 2
    # A reason alone is not enough: the caller must say --production.
    assert _run(tmp_path, "--reason", "x", "set", "KNOWLEDGE_ENABLED=true").returncode == 2
    assert _run(tmp_path, "--production", "set", "KNOWLEDGE_ENABLED=true").returncode == 2
    res = _run(tmp_path, "--production", "--reason", "owner asked", "set", "KNOWLEDGE_ENABLED=true")
    assert res.returncode == 0, res.stderr
    assert "KNOWLEDGE_ENABLED=true" in _lines(app / ".env")
    # A production NEXT_PUBLIC_ is written, and its build waits for a release.
    nxt = _run(tmp_path, "--production", "--reason", "r", "set", "NEXT_PUBLIC_X=1")
    assert nxt.returncode == 0, nxt.stderr
    assert "acb-flag-reapply" not in nxt.stdout
    assert "next release" in nxt.stdout
    # Production keeps no staging overlay, and logs the reason.
    assert not (tmp_path / "etc/acb/staging-flags.env").exists()
    assert "reason=owner\\ asked" in (tmp_path / "var/log/acb-flag.log").read_text(encoding="utf-8")
    assert _run(tmp_path, "--production", "--reason", "r", "reset", "X_ENABLED").returncode == 2


def test_no_deploy_env_line_means_production(tmp_path: pathlib.Path) -> None:
    """Production's .env has no DEPLOY_ENV line. The deploy reads that as production."""
    app = _box(tmp_path, env=None)
    assert _run(tmp_path, "set", "KNOWLEDGE_ENABLED=true").returncode == 2
    res = _run(tmp_path, "--production", "--reason", "r", "set", "KNOWLEDGE_ENABLED=true")
    assert res.returncode == 0, res.stderr
    assert "KNOWLEDGE_ENABLED=true" in _lines(app / ".env")


def test_staging_refuses_the_production_flag_and_production_refuses_a_deploy(
    tmp_path: pathlib.Path,
) -> None:
    _box(tmp_path / "s")
    assert _run(tmp_path / "s", "--production", "--reason", "x", "list").returncode == 2
    _box(tmp_path / "p", env="production")
    res = _run(tmp_path / "p", "--production", "--reason", "x", "deploy")
    assert res.returncode == 2
    assert "gh workflow run deploy.yml" in res.stderr


@pytest.mark.parametrize(
    "content",
    [
        "DEPLOY_ENV=dev\n",
        "export DEPLOY_ENV=staging\n",
        "DEPLOY_ENV=staging\nexport DEPLOY_ENV=production\n",
        " DEPLOY_ENV=staging\n",
    ],
)
def test_an_unknown_box_is_refused(tmp_path: pathlib.Path, content: str) -> None:
    app = tmp_path / "opt/acb/app"
    app.mkdir(parents=True)
    (app / ".env").write_text(content, encoding="utf-8")
    assert _run(tmp_path, "list").returncode == 2
    assert _run(tmp_path, "--production", "--reason", "x", "list").returncode == 2


def test_staging_deploy_starts_the_pull_unit(tmp_path: pathlib.Path) -> None:
    _box(tmp_path)
    res = _run(tmp_path, "deploy")
    assert res.returncode == 0, res.stderr
    assert "DRY: systemctl start --no-block acb-pull.service" in res.stdout


def test_the_test_seams_are_ignored_for_root() -> None:
    body = _TOOL.read_text(encoding="utf-8")
    assert re.search(r'if \[ "\$\(id -u\)" -ne 0 \]; then\n\s+root="\$\{ACB_FLAG_ROOT:-\}"', body)
    assert body.startswith("#!/bin/bash\n")
    assert "\nPATH=/usr/local/sbin:" in body


def test_the_tool_is_executable_in_git() -> None:
    """sudoers runs it by path, so git must keep the exec bit."""
    mode = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-s", "deploy/hostinger/acb-flag"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]
    assert mode == "100755"


def test_the_nightly_refresh_applies_the_overlay_after_production(tmp_path: pathlib.Path) -> None:
    """A staging flag on soak survives the night (spec: production's set plus the soak flag)."""
    body = _REFRESH.read_text(encoding="utf-8")
    assert body.index("apply_flags root") < body.index("apply_overlay root")
    overlay = tmp_path / "staging-flags.env"
    overlay.write_text(
        "root:KNOWLEDGE_ORGS=a,b\nroot:EMAIL_SYNC_ENABLED=true\nroot:DB_URL_ENABLED=x\n"
        "root:TASKS_LENS=$(id)\nroot:-OLD_ENABLED\nroot:-EMAIL_SYNC_ENABLED\n"
        "workbench:NEXT_PUBLIC_X=1\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "KNOWLEDGE_ORGS=prod-org\nEMAIL_SYNC_ENABLED=false\nOLD_ENABLED=true\nTASKS_LENS=on\n",
        encoding="utf-8",
    )
    wb = tmp_path / ".env.local"
    wb.write_text("", encoding="utf-8")
    held = re.search(r"^held='[^']+'$", body, re.M).group(0)
    fn = re.search(r"^apply_overlay\(\) \{.*?^\}", body, re.M | re.S).group(0)
    sw = re.search(r"^ov_switch='[^']+'$", body, re.M).group(0)
    prog = "\n".join(
        [
            "set -euo pipefail",
            f"overlay={overlay.as_posix()}",
            held,
            sw,
            fn,
            f"apply_overlay root {env.as_posix()}",
            f"apply_overlay workbench {wb.as_posix()}",
        ]
    )
    res = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    # The odd value is skipped, the removal record takes OLD_ENABLED out, and a
    # held switch is never touched, not even by a removal record.
    assert sorted(_lines(env)) == [
        "EMAIL_SYNC_ENABLED=false",
        "KNOWLEDGE_ORGS=a,b",
        "TASKS_LENS=on",
    ]
    assert _lines(wb) == ["NEXT_PUBLIC_X=1"]


def test_the_bootstrap_grants_dev_this_tool_and_nothing_else() -> None:
    body = _BOOT.read_text(encoding="utf-8")
    lines = re.findall(r"^\s*printf '(dev [^']*)\\n'", body, re.M)
    assert lines == ["dev ALL=(root) NOPASSWD: /opt/acb/app/deploy/hostinger/acb-flag"]
