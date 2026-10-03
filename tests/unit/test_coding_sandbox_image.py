"""WS43-F10 — the coding sandbox image keeps its pins (WS-43b).

Spec: ``project-docs/specs/maf_coding_engine.md`` §7.2 (the image), §10 (the
fence row WS43-F10) and the WS-43b slice.

Two halves.

**The static half** runs in the default unit job. It reads
``apps/services/orchestrator/Dockerfile.coding-sandbox`` and
``apps/services/orchestrator/sandbox/requirements.txt``, and it fails when:

- a ``FROM`` line or a ``COPY --from`` names an image with no digest,
- a requirement has no exact pin or no hash, or pip stops enforcing hashes or
  starts to accept an sdist (``--only-binary=:all:``),
- an ``ENV`` or ``ARG`` of any stage carries a credential name, in either form,
- Node loses its exact 22.x version or its SHA-256, or the build stops checking
  the tarball against that SHA-256,
- the final stage runs as root, or names ``copilot``,
- the default run stops deselecting ``sandbox_docker``, or a pytest command of
  the unit job passes its own ``-m`` (continuation lines included),
- the workflow stops failing on a skip, or its pull_request trigger gets a
  filter, so it stops reporting on every PR.

Each checker also runs on synthetic bad input. So a checker that goes blind is
a red test, and not a silent gap.

**The Docker half** carries the ``sandbox_docker`` marker, and the default run
deselects it (``pyproject.toml``). ``.github/workflows/sandbox-docker.yml``
builds the image once, gives its ID in ``CODING_SANDBOX_IMAGE``, and fails on
any skip. With no such variable, the fixture builds the image itself.

WS43-F10's last clause, "the broker accepts a mutable tag", is a broker test.
WS-43c added it at the end of this file: ``sandbox_broker.pinned_image()``
refuses a tag and accepts an image ID or a digest, the CI image included.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_ORCH = _REPO / "apps" / "services" / "orchestrator"
_DOCKERFILE = _ORCH / "Dockerfile.coding-sandbox"
_REQUIREMENTS = _ORCH / "sandbox" / "requirements.txt"
_WORKFLOWS = _REPO / ".github" / "workflows"

#: The base of §7.2, by name. The digest is the pin, and the name is the intent.
_BASE_NAME = "python:3.12-slim-bookworm"

#: The first set of §7.2: each distribution, and the module it imports as.
_FIRST_SET = {
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "matplotlib": "matplotlib",
    "pypdf": "pypdf",
    "fpdf2": "fpdf",
    "markdown": "markdown",
    "tabulate": "tabulate",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "requests": "requests",
}

#: §7.2: "It holds no secret, no SDK and no CLI." These are the SDKs that hold
#: a model client. None of them may enter the image.
_FORBIDDEN_DISTRIBUTIONS = (
    "github-copilot-sdk",
    "agent-framework",
    "openai",
    "anthropic",
    "litellm",
)
_FORBIDDEN_MODULES = ("copilot", "agent_framework", "openai", "anthropic", "litellm")

#: The credential words of ``acb_skills.code_tools._ENV_DENY_RE``.
_SECRET_NAME_RE = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL)", re.I)
#: ``GPG_KEY`` comes from the official python base. It is the public ID of the
#: CPython release signing key, and not a secret.
_PUBLIC_BASE_ENV = frozenset({"GPG_KEY"})

_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}(\s|$)")
_PIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.+!_-]*")
_IMMUTABLE_REF_RE = re.compile(r"^(sha256:[0-9a-f]{64}|[^\s@]+@sha256:[0-9a-f]{64})$")

_SHA_A = "a" * 64
_SHA_B = "b" * 64


# --------------------------------------------------------------------------
# Parsers and checkers. Each checker returns a list of problems, empty when
# the input is good, so that a synthetic bad input can prove it still sees.
# --------------------------------------------------------------------------


def _instructions(text: str) -> list[tuple[str, str]]:
    """Return ``(INSTRUCTION, arguments)`` for each logical Dockerfile line."""
    out: list[tuple[str, str]] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            # Docker drops a comment line, and a blank one, inside a
            # continuation too.
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        word, _, rest = buf.strip().partition(" ")
        out.append((word.upper(), rest.strip()))
        buf = ""
    if buf.strip():
        word, _, rest = buf.strip().partition(" ")
        out.append((word.upper(), rest.strip()))
    return out


def _final_stage(text: str) -> list[tuple[str, str]]:
    """Return the instructions after the last ``FROM``."""
    steps = _instructions(text)
    last = max((i for i, (word, _) in enumerate(steps) if word == "FROM"), default=-1)
    return steps[last + 1 :]


def _from_problems(text: str) -> list[str]:
    """Each image a build pulls must carry a digest, or name a stage of the file."""
    problems: list[str] = []
    stages: set[str] = set()
    bases: set[str] = set()
    seen_from = False
    for word, args in _instructions(text):
        if word == "FROM":
            seen_from = True
            parts = [p for p in args.split() if not p.startswith("--")]
            image = parts[0] if parts else ""
            is_stage = image.lower() in stages
            if not is_stage and not _DIGEST_RE.search(image):
                problems.append(f"FROM {image!r} has no @sha256: digest")
            elif not is_stage:
                bases.add(image)
                if image.split("@", 1)[0] != _BASE_NAME:
                    problems.append(f"FROM {image!r} is not {_BASE_NAME}")
            if len(parts) >= 3 and parts[1].lower() == "as":
                stages.add(parts[2].lower())
        elif word == "COPY":
            for match in re.finditer(r"--from=(\S+)", args):
                source = match.group(1)
                if source.lower() not in stages and not _DIGEST_RE.search(source):
                    problems.append(f"COPY --from={source!r} has no @sha256: digest")
    if not seen_from:
        problems.append("no FROM line")
    if len(bases) > 1:
        problems.append(f"two bases, and §7.2 names one: {sorted(bases)}")
    return problems


def _requirements(text: str) -> list[str]:
    """Return each requirement, with its continuation lines joined."""
    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((buf + line).strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _requirement_problems(text: str) -> list[str]:
    """Each requirement needs an exact ``==`` pin and a sha256 hash."""
    reqs = _requirements(text)
    if not reqs:
        return ["no requirement"]
    problems: list[str] = []
    for req in reqs:
        spec = req.split()[0]
        if spec.startswith("-"):
            problems.append(f"an option line, not a pinned requirement: {req[:80]!r}")
            continue
        if not _PIN_RE.fullmatch(spec):
            problems.append(f"no exact == pin: {spec!r}")
        if not _HASH_RE.search(req):
            problems.append(f"no --hash=sha256: {spec!r}")
    return problems


def _pinned_names(text: str) -> set[str]:
    """Return the normalised distribution names that the file pins."""
    names = set()
    for req in _requirements(text):
        spec = req.split()[0]
        if "==" in spec:
            names.add(re.sub(r"[-_.]+", "-", spec.split("==", 1)[0]).lower())
    return names


def _pip_problems(text: str) -> list[str]:
    """Each pip install must enforce hashes, and read the hashed file."""
    runs = [args for word, args in _instructions(text) if word == "RUN"]
    installs = [r for r in runs if re.search(r"\bpip3?\s+install\b", r)]
    if not installs:
        return ["no pip install"]
    problems: list[str] = []
    for run in installs:
        if "--require-hashes" not in run.split():
            problems.append("a pip install has no --require-hashes")
        if not re.search(r"--only-binary(=|\s+):all:(\s|$)", run):
            problems.append("a pip install has no --only-binary=:all:, so an sdist can run code")
        if "requirements.txt" not in run:
            problems.append("a pip install does not read requirements.txt")
    copies = [args for word, args in _instructions(text) if word == "COPY"]
    if not any("sandbox/requirements.txt" in c.split() for c in copies):
        problems.append("no COPY of sandbox/requirements.txt")
    return problems


def _declared(args: str) -> dict[str, str]:
    """Return the names, with their values, that one ENV or ARG line sets.

    Docker takes two forms. ``ENV A=1 B=2`` sets each ``name=value`` pair. The
    legacy ``ENV NAME value`` sets one name, and the rest of the line is the
    value. ``ARG NAME`` with no default sets the name to an empty value.
    """
    parts = args.split(maxsplit=1)
    if not parts:
        return {}
    if "=" not in parts[0]:
        value = parts[1].strip() if len(parts) > 1 else ""
        return {parts[0]: value.strip("\"'")}
    return {
        match.group(1): match.group(2).strip("\"'")
        for match in re.finditer(r"(?:^|\s)(\w+)=(\S*)", args)
    }


def _secret_env_problems(text: str) -> list[str]:
    """No ENV or ARG of any stage may carry a credential name, in either form."""
    return [
        f"{word} {name} has a secret-like name"
        for word, args in _instructions(text)
        if word in ("ENV", "ARG")
        for name in _declared(args)
        if _SECRET_NAME_RE.search(name)
    ]


def _node_problems(text: str) -> list[str]:
    """Node needs one exact 22.x version and a checked SHA-256 of the tarball."""
    steps = _instructions(text)
    env: dict[str, str] = {}
    args_declared: set[str] = set()
    for word, args in steps:
        if word in ("ENV", "ARG"):
            names = _declared(args)
            env.update(names)
            if word == "ARG":
                args_declared.update(names)
    problems: list[str] = []
    version = env.get("NODE_VERSION", "")
    if not re.fullmatch(r"22\.\d+\.\d+", version):
        problems.append(f"NODE_VERSION is not one exact 22.x.y version: {version!r}")
    sha = env.get("NODE_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        problems.append(f"NODE_SHA256 is not a sha256: {sha!r}")
    for name in sorted({"NODE_VERSION", "NODE_SHA256"} & args_declared):
        problems.append(f"{name} is an ARG, so a --build-arg can replace the pin")
    runs = " ".join(args for word, args in steps if word == "RUN")
    if "/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar" not in runs:
        problems.append("the download does not use the pinned version for linux-x64")
    if not re.search(
        r"echo\s+\"?\$\{NODE_SHA256\}\s+\S+?\"?\s*\|\s*sha256sum\s+(-c|--check)\b", runs
    ):
        problems.append("the build does not check the tarball against ${NODE_SHA256}")
    return problems


def _user_problems(text: str) -> list[str]:
    """The final stage must end on a non-root USER."""
    users = [args for word, args in _final_stage(text) if word == "USER"]
    if not users:
        return ["the final stage has no USER line, so it runs as root"]
    user = users[-1].split(":", 1)[0].strip()
    if user in ("root", "0"):
        return [f"the final stage runs as {users[-1]!r}"]
    return []


def _copilot_problems(text: str) -> list[str]:
    """No instruction of the image may name ``copilot``."""
    return [
        f"{word} names copilot" for word, args in _instructions(text) if "copilot" in args.lower()
    ]


def _workflow(text: str) -> dict[str, Any]:
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict), "the workflow is not a YAML mapping"
    return doc


def _triggers(doc: dict[str, Any]) -> dict[str, Any]:
    """YAML 1.1 reads the bare key ``on`` as the boolean True."""
    triggers = doc.get("on", doc.get(True))
    assert isinstance(triggers, dict), f"the workflow has no `on:` mapping: {triggers!r}"
    return triggers


def _run_blocks(doc: dict[str, Any], job: str) -> list[str]:
    """Return each ``run:`` block of one job, with its backslash continuations joined."""
    steps = doc["jobs"][job].get("steps") or []
    return [re.sub(r"\\[ \t]*\r?\n", " ", step["run"]) for step in steps if step.get("run")]


def _marker_overrides(doc: dict[str, Any], job: str) -> list[str]:
    """Return each pytest command of the job that passes its own ``-m``.

    A ``-m`` on the command line replaces the addopts filter, so that command
    would run the Docker tests again. ``python -m pytest`` carries a ``-m`` of
    its own, so only the text after ``pytest`` counts.
    """
    commands = [
        line.strip()
        for block in _run_blocks(doc, job)
        for line in block.splitlines()
        if re.search(r"\bpytest\b", line)
    ]
    return [c for c in commands if re.search(r"(?:^|\s)-m", c.split("pytest", 1)[1])]


# --------------------------------------------------------------------------
# The static half — the real files.
# --------------------------------------------------------------------------


def _dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def _requirements_text() -> str:
    return _REQUIREMENTS.read_text(encoding="utf-8")


def _pinned_node_version() -> str:
    match = re.search(r"\bNODE_VERSION=(\S+)", _dockerfile())
    assert match, "Dockerfile.coding-sandbox sets no NODE_VERSION"
    return match.group(1)


def test_every_base_is_pinned_by_digest() -> None:
    assert _from_problems(_dockerfile()) == []


def test_every_requirement_is_pinned_and_hashed() -> None:
    assert _requirement_problems(_requirements_text()) == []


def test_pip_installs_the_hashed_file_with_require_hashes() -> None:
    assert _pip_problems(_dockerfile()) == []


def test_node_is_pinned_by_version_and_sha256() -> None:
    assert _node_problems(_dockerfile()) == []


def test_the_final_stage_does_not_run_as_root() -> None:
    assert _user_problems(_dockerfile()) == []


def test_the_first_set_of_section_7_2_is_pinned() -> None:
    missing = set(_FIRST_SET) - _pinned_names(_requirements_text())
    assert not missing, f"§7.2 names these, and requirements.txt does not pin them: {missing}"


def test_the_image_holds_no_copilot_and_no_sdk() -> None:
    assert _copilot_problems(_dockerfile()) == []
    pinned = _pinned_names(_requirements_text())
    found = [d for d in _FORBIDDEN_DISTRIBUTIONS if any(n.startswith(d) for n in pinned)]
    assert not found, f"requirements.txt pins an SDK: {found}"


def test_the_dockerfile_sets_no_secret_env() -> None:
    assert _secret_env_problems(_dockerfile()) == []


#: The key of the unit-test job ("Unit tests") in pr-check.yml.
_UNIT_JOB = "test"


def test_the_default_run_deselects_sandbox_docker() -> None:
    """Done-when 5, first half: the unit job of pr-check.yml never builds the image."""
    config = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_config = config["tool"]["pytest"]["ini_options"]
    match = re.search(r"-m\s+'([^']*)'", pytest_config["addopts"])
    assert match, f"addopts has no -m filter: {pytest_config['addopts']!r}"
    assert re.search(r"\bnot sandbox_docker\b", match.group(1)), match.group(1)
    assert any(m.startswith("sandbox_docker:") for m in pytest_config["markers"])
    doc = _workflow((_WORKFLOWS / "pr-check.yml").read_text(encoding="utf-8"))
    assert _UNIT_JOB in doc["jobs"], f"pr-check.yml has no `{_UNIT_JOB}` job"
    blocks = _run_blocks(doc, _UNIT_JOB)
    assert any(re.search(r"\bpytest\s+tests/unit/?(\s|$)", b) for b in blocks), (
        "the unit job no longer runs tests/unit"
    )
    # EVERY pytest command of the job counts, the continuation lines included.
    assert _marker_overrides(doc, _UNIT_JOB) == []


def test_the_workflow_runs_sandbox_docker_and_fails_on_a_skip() -> None:
    """Done-when 5, second half: sandbox-docker.yml runs the marker and refuses a skip."""
    doc = _workflow((_WORKFLOWS / "sandbox-docker.yml").read_text(encoding="utf-8"))
    triggers = _triggers(doc)
    assert {"pull_request", "schedule", "workflow_dispatch"} <= set(triggers), triggers
    # No filter on the PR trigger: the check must report on EVERY pull request,
    # or it cannot be a required check (a filtered workflow reports nothing).
    filters = {"paths", "paths-ignore", "branches", "branches-ignore"}
    assert not filters & set(triggers["pull_request"] or {}), triggers["pull_request"]
    (job,) = doc["jobs"]
    steps = "\n".join(_run_blocks(doc, job))
    assert re.search(r"pytest -m sandbox_docker -rs\b", steps)
    assert "--junitxml=" in steps
    assert "CODING_SANDBOX_IMAGE=" in steps
    assert re.search(r"if skipped:\s+sys\.exit\(", steps), "the skip check is gone"
    assert re.search(r"if tests == 0:\s+sys\.exit\(", steps), "the no-test check is gone"


# --------------------------------------------------------------------------
# The checkers still see: one synthetic bad input for each clause.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"FROM {_BASE_NAME}\n", id="tag-only"),
        pytest.param("FROM python:latest\n", id="latest"),
        pytest.param(f"FROM {_BASE_NAME}@sha256:abc123\n", id="short-digest"),
        pytest.param(f"FROM debian:bookworm-slim@sha256:{_SHA_A}\n", id="other-base"),
        pytest.param(
            f"FROM {_BASE_NAME}@sha256:{_SHA_A} AS b\nFROM b\nCOPY --from=alpine:3 /x /x\n",
            id="copy-from-tag",
        ),
        pytest.param(
            f"FROM {_BASE_NAME}@sha256:{_SHA_A} AS b\nFROM {_BASE_NAME}@sha256:{_SHA_B}\n",
            id="two-bases",
        ),
        pytest.param("RUN true\n", id="no-from"),
    ],
)
def test_the_digest_check_refuses(text: str) -> None:
    assert _from_problems(text)


def test_the_digest_check_accepts_a_stage_name() -> None:
    text = (
        f"FROM {_BASE_NAME}@sha256:{_SHA_A} AS node\n"
        f"FROM {_BASE_NAME}@sha256:{_SHA_A}\n"
        "COPY --from=node /opt/node /opt/node\n"
    )
    assert _from_problems(text) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("pandas==3.0.6\n", id="no-hash"),
        pytest.param(f"pandas>=3 \\\n    --hash=sha256:{_SHA_A}\n", id="range-pin"),
        pytest.param(f"pandas \\\n    --hash=sha256:{_SHA_A}\n", id="no-pin"),
        pytest.param("pandas==3.0.6 \\\n    --hash=sha256:abc\n", id="short-hash"),
        pytest.param(
            f"numpy==2.5.3 \\\n    --hash=sha256:{_SHA_A}\npandas==3.0.6\n",
            id="one-line-of-two",
        ),
        pytest.param(
            f"--extra-index-url https://x.example\npandas==3.0.6 --hash=sha256:{_SHA_A}\n",
            id="option-line",
        ),
        pytest.param("# only a comment\n", id="empty"),
    ],
)
def test_the_hash_check_refuses(text: str) -> None:
    assert _requirement_problems(text)


def test_the_hash_check_accepts_a_hashed_pin() -> None:
    text = (
        "# a header\n"
        f"pandas==3.0.6 \\\n    --hash=sha256:{_SHA_A} \\\n    --hash=sha256:{_SHA_B}\n"
        "    # via -r -\n"
    )
    assert _requirement_problems(text) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "COPY sandbox/requirements.txt /r.txt\nRUN pip install -r /r.txt\n",
            id="no-require-hashes",
        ),
        pytest.param(
            "COPY sandbox/requirements.txt /r.txt\n"
            "RUN pip install --require-hashes -r /r.txt\nRUN pip install pandas\n",
            id="second-unhashed-install",
        ),
        pytest.param(
            "COPY sandbox/requirements.txt /r.txt\nRUN pip install --require-hashes -r /r.txt\n",
            id="no-only-binary",
        ),
        pytest.param(
            "COPY sandbox/requirements.txt /r.txt\n"
            "RUN pip install --require-hashes --only-binary=numpy -r /r.txt\n",
            id="only-binary-for-one-package",
        ),
        pytest.param("RUN pip install --require-hashes -r /requirements.txt\n", id="no-copy"),
        pytest.param("RUN true\n", id="no-install"),
    ],
)
def test_the_pip_check_refuses(text: str) -> None:
    assert _pip_problems(text)


@pytest.mark.parametrize("flag", ["--only-binary=:all:", "--only-binary :all:"])
def test_the_pip_check_accepts_a_hashed_wheel_only_install(flag: str) -> None:
    text = (
        "COPY sandbox/requirements.txt /opt/requirements.txt\n"
        f"RUN pip install --require-hashes {flag} \\\n    -r /opt/requirements.txt\n"
    )
    assert _pip_problems(text) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("ENV GITHUB_TOKEN placeholder\n", id="legacy-form"),
        pytest.param("ENV HOME=/tmp GITHUB_TOKEN=placeholder\n", id="pair-form"),
        pytest.param("ARG LLM_API_KEY\n", id="arg-no-default"),
        pytest.param("ARG DB_PASSWORD=placeholder\n", id="arg-default"),
        pytest.param(
            f"FROM {_BASE_NAME}@sha256:{_SHA_A} AS b\nENV SECRET placeholder\n"
            f"FROM {_BASE_NAME}@sha256:{_SHA_A}\n",
            id="builder-stage",
        ),
    ],
)
def test_the_secret_env_check_refuses(text: str) -> None:
    assert _secret_env_problems(text)


def test_the_secret_env_check_accepts_plain_names() -> None:
    text = f"ENV HOME=/tmp NODE_SHA256={_SHA_A}\nENV LANG C.UTF-8\nARG NODE_VERSION\n"
    assert _secret_env_problems(text) == []


def _unit_job(*runs: str) -> dict[str, Any]:
    steps = "".join(f"      - run: |\n{run}" for run in runs)
    return _workflow(f"jobs:\n  test:\n    steps:\n{steps}")


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(
            "          uv run python -m pytest tests/unit/test_x.py \\\n"
            "            -m sandbox_docker -q -rs\n",
            id="m-on-a-continuation-line",
        ),
        pytest.param(
            "          uv run python -m pytest tests/unit/ -x -v -m 'not integration'\n",
            id="m-on-the-same-line",
        ),
        pytest.param(
            "          echo start\n          uv run pytest tests/unit -msandbox_docker\n",
            id="m-glued-on-a-later-line",
        ),
    ],
)
def test_the_marker_override_check_refuses(run: str) -> None:
    doc = _unit_job("          uv run python -m pytest tests/unit/ -x -v\n", run)
    assert _marker_overrides(doc, "test")


def test_the_marker_override_check_accepts_python_dash_m() -> None:
    doc = _unit_job(
        "          uv run python -m pytest tests/unit/ -x -v\n",
        "          uv run python -m pytest tests/unit/test_a.py \\\n"
        "            tests/unit/test_b.py -v -rs --maxfail=1\n",
    )
    assert _marker_overrides(doc, "test") == []


_NODE_RUN = (
    'RUN url="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz"; \\\n'
    '    echo "${NODE_SHA256}  /tmp/node.tar.gz" | sha256sum -c -\n'
)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"ENV NODE_SHA256={_SHA_A}\n" + _NODE_RUN, id="no-version"),
        pytest.param(f"ENV NODE_VERSION=22 NODE_SHA256={_SHA_A}\n" + _NODE_RUN, id="major-only"),
        pytest.param(f"ENV NODE_VERSION=latest NODE_SHA256={_SHA_A}\n" + _NODE_RUN, id="latest"),
        pytest.param(f"ENV NODE_VERSION=24.1.0 NODE_SHA256={_SHA_A}\n" + _NODE_RUN, id="not-22"),
        pytest.param("ENV NODE_VERSION=22.23.3\n" + _NODE_RUN, id="no-sha"),
        pytest.param("ENV NODE_VERSION=22.23.3 NODE_SHA256=abc\n" + _NODE_RUN, id="short-sha"),
        pytest.param(
            f"ARG NODE_VERSION=22.23.3\nENV NODE_SHA256={_SHA_A}\n" + _NODE_RUN,
            id="version-is-an-arg",
        ),
        pytest.param(
            f"ENV NODE_VERSION=22.23.3 NODE_SHA256={_SHA_A}\n"
            'RUN url="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz"\n',
            id="no-checksum-step",
        ),
        pytest.param(
            f"ENV NODE_VERSION=22.23.3 NODE_SHA256={_SHA_A}\n"
            'RUN url="https://nodejs.org/dist/latest-v22.x/node-linux-x64.tar.gz"; \\\n'
            '    echo "${NODE_SHA256}  /tmp/node.tar.gz" | sha256sum -c -\n',
            id="unpinned-url",
        ),
    ],
)
def test_the_node_check_refuses(text: str) -> None:
    assert _node_problems(text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"FROM {_BASE_NAME}@sha256:{_SHA_A}\nRUN true\n", id="no-user"),
        pytest.param(f"FROM {_BASE_NAME}@sha256:{_SHA_A}\nUSER 0:0\n", id="uid-0"),
        pytest.param(f"FROM {_BASE_NAME}@sha256:{_SHA_A}\nUSER 1000\nUSER root\n", id="root"),
        pytest.param(
            f"FROM {_BASE_NAME}@sha256:{_SHA_A} AS b\nUSER 1000\nFROM b\n",
            id="user-in-an-earlier-stage-only",
        ),
    ],
)
def test_the_user_check_refuses(text: str) -> None:
    assert _user_problems(text)


def test_the_copilot_check_refuses() -> None:
    assert _copilot_problems("COPY --from=builder /copilot-cli /usr/local/bin/copilot\n")


# --------------------------------------------------------------------------
# The Docker half. Deselected by default (pyproject.toml); the workflow
# .github/workflows/sandbox-docker.yml runs it and fails on any skip.
# --------------------------------------------------------------------------

#: The flags of §7.1 rule 6 that change what the image itself must survive.
_RUN_FLAGS = (
    "--rm",
    "--read-only",
    "--tmpfs",
    "/tmp:rw,nosuid,nodev,size=256m",
    "--network",
    "none",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
)


def _docker(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _docker_unavailable() -> str | None:
    """Return why Docker cannot run here, or None when it can."""
    if shutil.which("docker") is None:
        return "no docker CLI on PATH"
    try:
        info = _docker("info", "--format", "{{.ServerVersion}}", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"`docker info` failed: {exc}"
    if info.returncode != 0:
        return f"no Docker daemon: {info.stderr.strip()[:300]}"
    return None


@pytest.fixture(scope="module")
def coding_sandbox_image(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Return an immutable reference to the image: the CI build, or a new one."""
    why = _docker_unavailable()
    if why:
        pytest.skip(f"sandbox_docker needs Docker: {why}")
    given = os.environ.get("CODING_SANDBOX_IMAGE", "").strip()
    if given:
        assert _IMMUTABLE_REF_RE.match(given), (
            f"CODING_SANDBOX_IMAGE={given!r} is not an image ID or a digest (§7.2)"
        )
        return given
    iidfile = tmp_path_factory.mktemp("coding-sandbox") / "iid"
    build = _docker(
        "build",
        "--iidfile",
        str(iidfile),
        "-f",
        str(_DOCKERFILE),
        str(_ORCH),
        timeout=1800,
    )
    assert build.returncode == 0, f"docker build failed:\n{build.stderr[-6000:]}"
    image = iidfile.read_text(encoding="utf-8").strip()
    assert _IMMUTABLE_REF_RE.match(image), image
    return image


def _run_in_image(image: str, uid: int, script: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "run",
        *_RUN_FLAGS,
        "--user",
        f"{uid}:{uid}",
        image,
        "bash",
        "-o",
        "pipefail",
        "-c",
        script,
    )


_IMPORT_SCRIPT = """\
set -e
echo "uid: $(id -u)"
# A uid with no passwd entry gets HOME=/, and --read-only makes that unwritable.
# pip, npm and matplotlib write under HOME, so the image must point it at /tmp.
touch "$HOME/.home-probe"
echo "home: $HOME"
python - <<'PY'
import importlib
for name in {modules!r}:
    module = importlib.import_module(name)
    # A dir that this uid cannot read still imports, as an empty namespace
    # package with no __file__. So an import alone proves nothing.
    assert module.__file__, f"{{name}} imported as an empty namespace package"
import pandas
assert int(pandas.DataFrame({{"a": [1, 2]}})["a"].sum()) == 3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.plot([1, 2, 3])
plt.savefig("/tmp/plot.png")
print("imports: ok")
PY
echo "node: $(node --version)"
echo "npm: $(npm --version)"
echo "git: $(git --version)"
"""


@pytest.mark.sandbox_docker
@pytest.mark.parametrize("uid", [1000, 4242])
def test_the_image_runs_read_only_as_any_uid(coding_sandbox_image: str, uid: int) -> None:
    """Done-when 3: --read-only, as uid 1000 and as uid 4242."""
    script = _IMPORT_SCRIPT.format(modules=tuple(_FIRST_SET.values()))
    result = _run_in_image(coding_sandbox_image, uid, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    lines = dict(line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line)
    assert lines.get("uid") == str(uid)
    assert lines.get("home") == "/tmp"
    assert lines.get("imports") == "ok"
    assert lines.get("node") == f"v{_pinned_node_version()}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", lines.get("npm", "")), lines
    assert lines.get("git", "").startswith("git version "), lines


@pytest.mark.sandbox_docker
def test_copilot_is_not_on_path_and_no_sdk_is_installed(coding_sandbox_image: str) -> None:
    """Done-when 4: no Copilot CLI, no SDK."""
    script = (
        'if command -v copilot; then echo "copilot: found"; else echo "copilot: absent"; fi\n'
        "python - <<'PY'\n"
        "import importlib.util\n"
        f"modules = {json.dumps(list(_FORBIDDEN_MODULES))}\n"
        'print("sdk:", [m for m in modules if importlib.util.find_spec(m)])\n'
        "PY\n"
    )
    result = _run_in_image(coding_sandbox_image, 4242, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "copilot: absent" in result.stdout.splitlines(), result.stdout
    assert "sdk: []" in result.stdout.splitlines(), result.stdout


@pytest.mark.sandbox_docker
def test_the_image_config_holds_no_secret_and_no_root(coding_sandbox_image: str) -> None:
    """Done-when 4: no secret in the image's environment, and a non-root USER."""
    inspect = _docker("image", "inspect", "--format", "{{json .Config}}", coding_sandbox_image)
    assert inspect.returncode == 0, inspect.stderr
    config = json.loads(inspect.stdout)
    names = [entry.split("=", 1)[0] for entry in config.get("Env") or []]
    secret_like = [n for n in names if _SECRET_NAME_RE.search(n) and n not in _PUBLIC_BASE_ENV]
    assert not secret_like, f"the image sets a secret-like variable: {secret_like}"
    assert config.get("User", "").split(":", 1)[0] not in ("", "root", "0"), config.get("User")


# --------------------------------------------------------------------------
# WS43-F10's last clause (WS-43c): the broker runs an immutable reference only.
# --------------------------------------------------------------------------


class _ImageSetting:
    def __init__(self, image: str) -> None:
        self.sandbox_image = image


@pytest.mark.parametrize("ref", [
    "metorite/coding-sandbox:latest",
    "metorite/coding-sandbox",
    "python:3.12-slim-bookworm",
    "registry.example/coding-sandbox:2026-10-03",
    "coding-sandbox@sha256:abc123",
    "sha256:" + "a" * 63,
])
def test_the_broker_refuses_a_mutable_image_reference(ref: str) -> None:
    """§7.2 Pinning: a tag can move under the box, so the broker refuses it."""
    from orchestrator import sandbox_broker

    with pytest.raises(sandbox_broker.SandboxRefused, match="pinned"):
        sandbox_broker.pinned_image(_ImageSetting(ref))


def test_the_broker_accepts_an_image_id_and_a_digest() -> None:
    """The ID that the CI build gives, and a registry digest, both pass."""
    from orchestrator import sandbox_broker

    refs = [
        "sha256:" + "0" * 64,
        "python:3.12-slim-bookworm@sha256:" + "1" * 64,
        "registry.example/coding-sandbox@sha256:" + "2" * 64,
    ]
    given = os.environ.get("CODING_SANDBOX_IMAGE", "").strip()
    if given:
        refs.append(given)
    for ref in refs:
        assert _IMMUTABLE_REF_RE.match(ref), ref
        assert sandbox_broker.pinned_image(_ImageSetting(ref)) == ref
