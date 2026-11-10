"""Guards for the meeting-bot's deployment wiring.

The worker only ever runs on the VPS, so a mistake here (a port collision with
the gateway, a service that quietly joins the core profile, a missing shared
secret) wouldn't show up in any other test — it would show up as a broken
deploy. These pin the contract between the compose service and the deploy step.
"""
from __future__ import annotations

import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "infra/docker-compose.yml"
_WORKFLOW = _ROOT / ".github/workflows/deploy.yml"
# WS-25: the remote deploy script used to be 437 lines inside deploy.yml's
# `env:` block, so these guards read the workflow. It now lives in a versioned
# file that BOTH delivery paths execute — the workflow copies it over SSH, and
# scripts/vps_pull.sh runs it when the box updates itself. The assertions below
# are about the script's content, so they follow the script.
_APPLY = _ROOT / "scripts/vps_apply.sh"

# The gateway's own port on the VPS (deploy/hostinger/acb-gateway.service).
_GATEWAY_PORT = "8080"


def _service() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]["meeting-bot"]


def test_meeting_bot_is_not_in_the_core_profile() -> None:
    """Each in-call bot is a real Chrome (~1-3 GB). It must never start just
    because someone brought up the core stack."""
    profiles = _service()["profiles"]
    assert "meetingbot" in profiles
    assert "core" not in profiles


def test_published_ports_stay_on_loopback_and_clear_of_the_gateway() -> None:
    """The gateway owns 8080 on the VPS; publishing the bot there would break
    the whole app rather than just the bot. And EVERY published port must be
    loopback-only — one of them is an unauthenticated VNC view of a browser
    holding a Google session."""
    ports = _service()["ports"]
    assert ports, "the worker must publish its API"
    for mapping in ports:
        # "127.0.0.1:8095:8080" → bound to loopback, host 8095, container 8080
        host_side = str(mapping).split(":")
        assert host_side[0] == "127.0.0.1", f"{mapping} is reachable off-box"
        assert host_side[1] != _GATEWAY_PORT, f"{mapping} collides with the gateway"


def test_chrome_gets_enough_shared_memory() -> None:
    """Docker's default 64 MB /dev/shm makes Chrome crash on real pages."""
    assert _service()["shm_size"] == "1gb"


def test_worker_can_reach_the_gateway_for_live_callbacks() -> None:
    assert "host.docker.internal:host-gateway" in _service()["extra_hosts"]


def test_recordings_survive_a_container_restart() -> None:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    assert any("/data" in str(v) for v in _service()["volumes"])
    assert "acb-meeting-bot-data" in compose["volumes"]


# ── deploy step ──────────────────────────────────────────────────────────────

def test_both_delivery_paths_execute_the_same_apply_script() -> None:
    """Every guard below reads scripts/vps_apply.sh. That is only meaningful
    while something actually RUNS that file — otherwise the assertions pass
    against an orphan while the real deploy logic lives somewhere else, which is
    exactly the drift WS-25 extracted the script to prevent.

    Two consumers, both pinned: the workflow (push path) and the box's poller
    (pull path). If either stops using it, this fails loudly rather than letting
    the other tests certify a file nobody executes.
    """
    assert _APPLY.exists(), "the apply script must exist for these guards to mean anything"

    workflow = _WORKFLOW.read_text(encoding="utf-8")
    # Must be a RUNNABLE reference, not a mention. The first version of this
    # assertion was a bare substring check, and it stayed green when the
    # workflow was mutated to copy a different file — because the header
    # comments discuss vps_apply.sh by name. A guard satisfied by prose is not
    # a guard; it certifies the documentation, not the wiring.
    wired = [
        ln for ln in workflow.splitlines()
        if "vps_apply.sh" in ln and not ln.strip().startswith("#")
    ]
    assert wired, "the workflow must EXECUTE scripts/vps_apply.sh, not just mention it"
    # The 437 lines must not have been re-inlined alongside it — two copies is
    # worse than either one alone, because they drift silently.
    assert "DEPLOY_SCRIPT" not in workflow, "the script must not be inlined again"

    puller = (_ROOT / "scripts/vps_pull.sh").read_text(encoding="utf-8")
    assert "vps_apply.sh" in puller, "the box's poller must run the same script"


def test_deploy_wires_the_gateway_to_the_worker() -> None:
    """Without these the bot would run but the gateway couldn't dispatch to it."""
    deploy = _APPLY.read_text(encoding="utf-8")
    assert 'upsert_env MEETING_BOT_URL "http://127.0.0.1:8095"' in deploy
    assert 'upsert_env NOTES_BOT_PROVIDER "selfhosted"' in deploy
    # The worker calls back from inside its container, so localhost won't do.
    assert 'NOTES_LIVE_CALLBACK_BASE "http://host.docker.internal:8080"' in deploy


def test_deploy_generates_the_shared_secret_once() -> None:
    deploy = _APPLY.read_text(encoding="utf-8")
    assert "MEETING_BOT_TOKEN=$_mbtoken" in deploy
    # Only generated when absent — regenerating each deploy would break the
    # gateway/worker pair until both restarted.
    assert "grep -qE '^MEETING_BOT_TOKEN=.+' \"$ENV_FILE\"" in deploy


def test_deploy_passes_the_root_env_file_explicitly() -> None:
    """Compose resolves a bare .env against the project dir, so the
    substitutions would silently come out empty. Since WS-49 BH-6 every
    compose call goes through acb_compose, which names /etc/acb/root.env and
    the root copy of the project dir."""
    deploy = _APPLY.read_text(encoding="utf-8")
    assert "acb_compose --timeout 900 --profile meetingbot" in deploy
    assert "acb_compose --profile meetingbot rm -sf meeting-bot" in deploy
    helper = deploy[deploy.index("acb_compose() {"):]
    helper = helper[: helper.index("\n}\n")]
    assert "--env-file /etc/acb/root.env" in helper
    assert "--project-directory /usr/local/lib/acb/infra" in helper


def test_meeting_bot_failure_cannot_fail_the_deploy() -> None:
    """A bot build hiccup must skip the bot, never take down a deploy — and it
    must SAY SO loudly, because the old container keeps serving and that reads
    as a successful deploy (exactly what happened on 2026-07-30)."""
    deploy = _APPLY.read_text(encoding="utf-8")
    assert "meeting bot build/start FAILED" in deploy
    assert "PREVIOUS image" in deploy, "must name the stale-image state"


def test_meeting_bot_build_cannot_hang_the_deploy() -> None:
    """The 2026-07-30 outage: novnc pulled in tzdata, whose postinst prompted
    for a timezone, the build hung ~15 min until the deploy's SSH session died,
    and the deploy reported success while the OLD image stayed live.

    Two independent guards, both required: no tty for a prompt to read, and a
    hard timeout so a hang can never consume the deploy.
    """
    deploy = _APPLY.read_text(encoding="utf-8")
    build = [
        ln for ln in deploy.splitlines()
        if "up -d --build meeting-bot" in ln
    ]
    assert build, "the deploy must build the meeting bot somewhere"
    window = deploy[deploy.index("MB_IMAGE_BEFORE"):]
    assert "timeout 900" in window, "the build must be time-bounded"
    assert "</dev/null" in window, "no tty — an apt prompt must fail, not hang"


def test_the_dockerfile_installs_packages_noninteractively() -> None:
    """The root cause, guarded at its source: any apt install in this image
    must be non-interactive, or a dependency's postinst can hang the build."""
    dockerfile = (
        pathlib.Path(__file__).resolve().parents[2]
        / "apps/services/meeting_bot/Dockerfile"
    ).read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        if "apt-get install" in line:
            assert "DEBIAN_FRONTEND=noninteractive" in line, line


def test_live_caption_credentials_are_wired_by_default() -> None:
    """Without a token URL the bot joins, records, and emits no captions — a
    failure that looks like success until the meeting ends. The deploy sets it
    rather than leaving it opt-in, and compose has to pass it through."""
    env = _service()["environment"]
    assert "LIVE_TOKEN_URL" in env, "compose must pass the token URL to the worker"

    deploy = _APPLY.read_text(encoding="utf-8")
    assert "NOTES_LIVE_TOKEN_URL" in deploy
    assert "/notes/stt/bot-live-token" in deploy
    # The worker calls the gateway from inside its container, so it can't use
    # 127.0.0.1 (that would be the container itself).
    assert "host.docker.internal:8080/notes/stt/bot-live-token" in deploy
