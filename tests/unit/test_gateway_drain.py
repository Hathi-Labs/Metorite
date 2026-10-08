"""A gateway restart is a gap shorter than the holds that hide it (H-60 follow-up).

Measured on 2026-10-08. Sixteen gateway restarts. Most of them were down for
11 to 16 s, which Caddy's 30 s hold and the workbench's 25 s retry hide. Four
were not: uvicorn's drain after SIGTERM had no bound and ran 30, 32, 90 and
90 s, the last two ending in SIGKILL. The port is closed for the whole drain,
so each one showed "Metorite is updating" to every member.

The fix has two halves, and this file fences both:

1. ``deploy/hostinger/gateway-drain.sh`` runs as ``ExecStop=``, BEFORE the
   stop signal, while the old process still serves. It waits for running chat
   answers and Projects agent runs, read from ``GET /internal/drain``.
2. ``--timeout-graceful-shutdown`` bounds the drain after the signal, so the
   gap is that bound plus the start.
"""
from __future__ import annotations

import http.server
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy/hostinger/acb-gateway.service"
SCRIPT = ROOT / "deploy/hostinger/gateway-drain.sh"
GATEWAY_FETCH = ROOT / "workbench/control_plane/src/lib/gatewayFetch.ts"
MAIN = ROOT / "apps/services/gateway/gateway/main.py"

#: The slowest start measured on the box, from "Started acb-gateway.service" to
#: "Uvicorn running", on 2026-10-08 (09:33:05 to 09:33:21). Python imports
#: take nearly all of it; `uv run` takes 0.09 s.
WORST_START_S = 16


def _unit() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ln in UNIT.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.lstrip().startswith(("#", ";")):
            k, v = ln.split("=", 1)
            out.setdefault(k.strip(), []).append(v.strip())
    return out


def _drain_bound_s() -> int:
    (start,) = _unit()["ExecStart"]
    m = re.search(r"--timeout-graceful-shutdown[ =](\d+)", start)
    assert m, "ExecStart has no --timeout-graceful-shutdown, so the drain has no bound"
    return int(m.group(1))


def _retry_deadline_s() -> float:
    src = GATEWAY_FETCH.read_text(encoding="utf-8")
    m = re.search(r"deadlineMs:\s*([\d_]+)", src)
    assert m, "GATEWAY_RETRY.deadlineMs moved; read it from its new home"
    return int(m.group(1).replace("_", "")) / 1000


def _script_wait_s() -> int:
    m = re.search(r'GATEWAY_DRAIN_WAIT_SECONDS:-(\d+)', SCRIPT.read_text(encoding="utf-8"))
    assert m
    return int(m.group(1))


class TestTheRestartGapFitsTheHolds:
    def test_the_drain_has_a_bound(self) -> None:
        assert 1 <= _drain_bound_s()

    def test_the_drain_plus_the_start_fits_the_workbench_retry(self) -> None:
        # The workbench calls the gateway on localhost, so Caddy's hold does not
        # cover it. Its own retry does, and it is the shorter of the two.
        gap = _drain_bound_s() + WORST_START_S
        assert gap < _retry_deadline_s(), (
            f"a restart can be down {gap}s, and the workbench retries only "
            f"{_retry_deadline_s()}s, so members see 'Metorite is updating'"
        )

    def test_the_stop_step_runs_the_drain_script_from_the_checkout(self) -> None:
        (stop,) = _unit()["ExecStop"]
        assert stop == "/bin/bash /opt/acb/app/deploy/hostinger/gateway-drain.sh"
        assert SCRIPT.is_file()

    def test_systemd_outwaits_the_script(self) -> None:
        # TimeoutStopSec= bounds EACH ExecStop= command. A bound below the
        # script's own wait makes systemd kill the script and send SIGTERM
        # early, which is safe, but then the wait does not do its job.
        (timeout,) = _unit()["TimeoutStopSec"]
        # The script's last poll can start just before its bound: one poll
        # interval plus one curl of at most 2 s, and a margin.
        assert int(timeout) >= _script_wait_s() + 1 + 2 + 5

    def test_systemd_outwaits_the_drain(self) -> None:
        # The same bound then covers SIGTERM to exit. Below the drain plus the
        # lifespan shutdown, systemd would SIGKILL a gateway that is closing
        # cleanly, and lose the audit flush at the end of the lifespan.
        (timeout,) = _unit()["TimeoutStopSec"]
        assert int(timeout) >= _drain_bound_s() + 20

    def test_the_apply_installs_the_unit_before_it_restarts_the_gateway(self) -> None:
        apply = (ROOT / "scripts/vps_apply.sh").read_text(encoding="utf-8")
        cp = apply.index('deploy/hostinger/acb-gateway.service" /etc/systemd/system/')
        reload_ = apply.index("sudo systemctl daemon-reload", cp)
        restart = apply.index("sudo systemctl restart acb-gateway", cp)
        assert cp < reload_ < restart


# ── The script, run for real against a real HTTP server ─────────────────────


def _bash() -> str | None:
    if os.name == "nt":
        # Not WSL's bash.exe: a WSL shell cannot reach a Windows 127.0.0.1.
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


BASH = _bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash with curl on this machine")


class _Counts:
    """A fake gateway: answers each GET with the next status and count."""

    def __init__(self, answers: list[tuple[int, str]]) -> None:
        self.answers = answers
        self.seen_auth: list[str | None] = []
        self.calls = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.seen_auth.append(self.headers.get("Authorization"))
                i = min(outer.calls, len(outer.answers) - 1)
                outer.calls += 1
                status, body = outer.answers[i]
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *_a: object) -> None:
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/internal/drain"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _runs(n: int) -> tuple[int, str]:
    return 200, f'{{"runs":{n},"chat":{n},"projects":0}}'


def _run_script(url: str, wait_s: int = 5) -> tuple[subprocess.CompletedProcess[str], float]:
    env = {
        **os.environ,
        "GATEWAY_DRAIN_URL": url,
        "GATEWAY_DRAIN_WAIT_SECONDS": str(wait_s),
        "GATEWAY_DRAIN_POLL_SECONDS": "0.2",
        "GATEWAY_INTERNAL_TOKEN": "tok-123",
    }
    t0 = time.monotonic()
    out = subprocess.run(
        [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60,
    )
    return out, time.monotonic() - t0


@needs_bash
class TestTheDrainScript:
    def test_it_waits_until_every_run_ends(self) -> None:
        fake = _Counts([_runs(2), _runs(1), _runs(1), _runs(0)])
        try:
            out, _ = _run_script(fake.url)
        finally:
            fake.close()
        assert out.returncode == 0
        assert fake.calls == 4, "it stopped polling before the count reached 0"
        assert "every run ended" in out.stdout

    def test_it_sends_the_internal_token(self) -> None:
        fake = _Counts([_runs(0)])
        try:
            _run_script(fake.url)
        finally:
            fake.close()
        assert fake.seen_auth == ["Bearer tok-123"]

    def test_a_run_that_never_ends_cannot_hold_the_stop(self) -> None:
        fake = _Counts([_runs(1)])
        try:
            out, took = _run_script(fake.url, wait_s=1)
        finally:
            fake.close()
        assert out.returncode == 0
        assert 1 <= took < 10
        assert "still going" in out.stdout

    def test_zero_runs_stops_at_once(self) -> None:
        fake = _Counts([_runs(0)])
        try:
            out, took = _run_script(fake.url, wait_s=30)
        finally:
            fake.close()
        assert out.returncode == 0 and took < 10 and fake.calls == 1

    def test_an_older_gateway_with_no_route_stops_at_once(self) -> None:
        # The first deploy runs the NEW script against the OLD process.
        fake = _Counts([(404, '{"detail":"Not Found"}')])
        try:
            out, took = _run_script(fake.url, wait_s=30)
        finally:
            fake.close()
        assert out.returncode == 0 and took < 10
        assert "no count" in out.stdout

    def test_a_body_with_no_count_stops_at_once(self) -> None:
        fake = _Counts([(200, '{"status":"ok"}')])
        try:
            out, took = _run_script(fake.url, wait_s=30)
        finally:
            fake.close()
        assert out.returncode == 0 and took < 10

    def test_a_crashed_gateway_stops_at_once(self) -> None:
        fake = _Counts([_runs(0)])
        url = fake.url
        fake.close()  # nothing listens on the port now
        out, took = _run_script(url, wait_s=30)
        assert out.returncode == 0 and took < 10

    def test_a_count_that_stops_answering_mid_wait_stops_the_wait(self) -> None:
        fake = _Counts([_runs(3), (500, "{}")])
        try:
            out, took = _run_script(fake.url, wait_s=30)
        finally:
            fake.close()
        assert out.returncode == 0 and took < 10
        assert "stopped answering" in out.stdout


# ── The route ────────────────────────────────────────────────────────────────


class _Task:
    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import drain

    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "tok-123")
    app = FastAPI()
    app.include_router(drain.router)
    return TestClient(app)


class TestTheDrainRoute:
    def test_it_refuses_a_caller_without_the_internal_token(self, client) -> None:
        assert client.get("/internal/drain").status_code == 401
        bad = client.get("/internal/drain", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401

    def test_it_counts_live_chat_and_projects_runs_and_skips_ended_ones(
        self, client, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from gateway.routes.projects import agent_dispatch
        from orchestrator import stream_relay

        monkeypatch.setattr(stream_relay, "_DETACHED_TASKS", {
            "t1": _Task(False), "t2": _Task(False), "t3": _Task(True),
        })
        monkeypatch.setattr(agent_dispatch, "_RUNS", {_Task(False), _Task(True)})
        r = client.get("/internal/drain", headers={"Authorization": "Bearer tok-123"})
        assert r.status_code == 200
        assert r.json() == {"runs": 3, "chat": 2, "projects": 1}

    def test_an_idle_gateway_reads_zero(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from gateway.routes.projects import agent_dispatch
        from orchestrator import stream_relay

        monkeypatch.setattr(stream_relay, "_DETACHED_TASKS", {})
        monkeypatch.setattr(agent_dispatch, "_RUNS", set())
        r = client.get("/internal/drain", headers={"Authorization": "Bearer tok-123"})
        assert r.json()["runs"] == 0

    def test_the_gateway_mounts_the_route_outside_a_try(self) -> None:
        # A mount inside `try: ... except: pass` fails silently, and the stop
        # step then reads a 404 on every restart and never waits.
        src = MAIN.read_text(encoding="utf-8")
        i = src.index("from gateway.routes.drain import router as _drain_router")
        line_start = src.rindex("\n", 0, i) + 1
        assert src[line_start:i] == "", "the drain import is indented, so it sits in a block"
        assert "app.include_router(_drain_router)\n" in src
