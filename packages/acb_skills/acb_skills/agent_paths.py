"""Where an agent's code lives, and where each tenant's state lives.

Spec: project-docs/specs/agent_architecture.md §2
      project-docs/specs/memory_architecture.md §5.3

THE PROBLEM THIS SOLVES
-----------------------
``{agents_clone_dir}/repos/{agent}/`` has always been one directory doing two
jobs::

    repos/email-assistant/
        agents.py, config.json, instructions.md   CODE  — versioned, identical
                                                          for every user
        agent-data/, inputs/, outputs/            STATE — mutable, and different
                                                          for every user

Those have opposite lifecycles. Code is replaced wholesale by a ``git pull``;
state must survive one. Code is the same for everyone; state is the thing that
must NOT be. Conflating them is why a personal agent could not get its own
workspace without also forking its source checkout, and why
:func:`acb_memory.blob_store.rehydrate_workspace` restoring into a shared
directory leaks one person's notes in front of the next person's run.

THE SPLIT
---------
::

    code   {agents_clone_dir}/repos/{agent}/            one per agent
    state  {agents_clone_dir}/repos/{agent}/            when instance == ''
           {agents_clone_dir}/state/{agent}/{slug}/     when instance != ''

``instance`` is the key migration 136 added to ``agent_blob``; this module is
the disk agreeing with the database. The vocabulary is the manifest's
(:meth:`acb_skills.manifest.AgentManifest.instance_key`): ``''`` shared,
``u:<email>`` personal, ``t:<team>`` team.

``o:<organization_id>`` is the fourth key (H-201 part 3,
``projects_ai_chat.md`` §21.15). It is the working dir of a SHARED agent for
one tenant. The executor gives it to a run whose manifest key is ``''``, and
it takes the organization from the run binding, never from input. So the code
stays in the one clone, and each tenant's run output, inputs and agent-data
live in a folder of their own. The blob rows of that folder carry the same
key. See :func:`tenant_instance`.

WHY ``instance=''`` RETURNS THE OLD PATH
----------------------------------------
Deliberately, and it is the whole safety argument. Every agent that has not
declared ``sharing.instancing`` resolves to ``''``, so
:func:`agent_state_dir` hands back *byte-identically* the path the loader,
the file browsers and the artifact viewer already use. Those agents cannot
regress, because nothing about them changed. Only an agent that explicitly
declares itself ``personal`` or ``team`` gets a new directory.

That the runtime tolerates "working directory is not the code directory" is
not a hope: ``config.json``'s ``workspace_root`` has always pointed agents at
external repos, and :func:`orchestrator.executor._resolve_effective_agent_dir`
has always honoured it.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

__all__ = [
    "AGENT_NAME_RE",
    "RUN_DATA_DIR",
    "SKILLS_REL",
    "SKILL_AUTHOR_MARKER",
    "TENANT_INSTANCE_PREFIX",
    "InvalidAgentName",
    "agent_code_dir",
    "agent_state_dir",
    "claim_skill",
    "clone_root",
    "ensure_state_dir",
    "instance_slug",
    "is_other_thread_rel",
    "is_tenant_instance",
    "is_thread_slug",
    "is_valid_agent_name",
    "refused_write",
    "require_agent_name",
    "run_data_rel",
    "skill_author",
    "skill_top_rel",
    "state_root",
    "tenant_instance",
    "thread_outputs_rel",
    "thread_slug",
    "upload_dir_rel",
    "workspace_blob_key",
]

# A state directory records its own partition key. The slug in the path is
# one-way (hash-suffixed), so without this marker nothing could map a
# directory back to the instance its blobs are stored under — and deriving it
# from "whoever is asking" breaks the moment a shared session lets a second
# person open the first person's workspace.
_INSTANCE_MARKER = ".cc-instance"

#: The ONE rule for an agent name that becomes a path segment (H-201 fix
#: round 2). One segment: a letter or digit first, then letters, digits,
#: ``.``, ``_`` or ``-``, at most 64 characters. So ``.``, ``..``, a slash, a
#: NUL and an absolute path can never pass. A name arrives as data from a
#: request, a session row or a registry row, and ``repos/{name}`` must stay
#: inside ``repos/``. Every filesystem use of an agent name goes through
#: :func:`require_agent_name` or :func:`is_valid_agent_name`.
AGENT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class InvalidAgentName(ValueError):
    """An agent name that is not one safe path segment."""


def is_valid_agent_name(name: object) -> bool:
    """True when *name* is one safe path segment (:data:`AGENT_NAME_RE`)."""
    return isinstance(name, str) and AGENT_NAME_RE.fullmatch(name) is not None


def require_agent_name(name: object) -> str:
    """*name*, or :class:`InvalidAgentName` when it is not one safe segment."""
    if not is_valid_agent_name(name):
        raise InvalidAgentName(f"not a valid agent name: {str(name)[:80]!r}")
    return str(name)


# Anything outside this set is replaced in a slug. ':' (in ``u:``/``t:``) is
# illegal in Windows filenames and awkward in shell paths everywhere.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Keep the readable part short enough that {slug} sits far inside the 255-byte
# filename limit even for a long team name or address. Collisions introduced by
# truncation are resolved by the digest, which is computed over the FULL key.
_SLUG_READABLE_MAX = 48


def _configured_clone_dir() -> Path:
    """The configured clone root, matching every other consumer's fallback."""
    from acb_common import get_settings

    settings = get_settings()
    return Path(
        getattr(settings, "agents_clone_dir", str(Path.home() / ".acb" / "agents"))
    )


def clone_root() -> Path:
    """``{agents_clone_dir}/repos`` — where agent checkouts live."""
    return _configured_clone_dir() / "repos"


def state_root() -> Path:
    """``{agents_clone_dir}/state`` — where per-tenant workspaces live.

    A sibling of ``repos/`` rather than a child, so a ``git clean`` or a
    re-clone inside ``repos/`` can never take a tenant's data with it.
    """
    return _configured_clone_dir() / "state"


def instance_slug(instance: str) -> str:
    """A filesystem-safe, collision-free directory name for an instance key.

    ``"u:alice@fracktal.in"`` → ``"u_alice_fracktal.in-3f2a9c11"``

    Readable enough to debug by ``ls``, and suffixed with a digest of the FULL
    key so two instances can never share a directory — which matters because
    sharing one would be exactly the leak this module exists to prevent.
    Distinct keys that sanitise to the same string (``a+b@x.com`` and
    ``a_b@x.com``) still get distinct directories.
    """
    if not instance:
        return ""
    digest = hashlib.sha256(instance.encode("utf-8")).hexdigest()[:8]
    readable = _UNSAFE.sub("_", instance)[:_SLUG_READABLE_MAX].strip("._-")
    return f"{readable}-{digest}" if readable else digest


#: The prefix of a tenant key. ``o`` for organization, so it can never be read
#: as ``t:<team>``, which is keyed by a team name alone.
TENANT_INSTANCE_PREFIX = "o:"


def tenant_instance(organization_id: object) -> str:
    """``o:<organization_id>``, the key of a shared agent's working dir for one
    tenant (H-201 part 3).

    The caller passes the run's tenant from the run binding (``_RUN_ORG`` or
    ``current_tenant``) or the caller's authenticated identity, never a value
    from a request body. An empty tenant raises ``ValueError``: no tenant means
    no working dir, and the caller must fail closed.
    """
    org = str(organization_id or "").strip()
    if not org:
        raise ValueError("a tenant working dir needs an organization id")
    return f"{TENANT_INSTANCE_PREFIX}{org}"


def is_tenant_instance(instance: object) -> bool:
    """True when *instance* is an ``o:<organization_id>`` key."""
    return (
        isinstance(instance, str)
        and instance.startswith(TENANT_INSTANCE_PREFIX)
        and len(instance) > len(TENANT_INSTANCE_PREFIX)
    )


# ── The thread's own output folder and run data (D86, maf_coding_engine §16.3) ──

#: A thread slug: the readable part and the 8 hex digits of :func:`instance_slug`.
_THREAD_SLUG_RE = re.compile(
    r"(?P<readable>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)-(?P<digest>[0-9a-f]{8})"
)

#: The dir under :func:`state_root` that holds the run data of every thread.
#: Its name starts with a dot, so it can never be an agent name
#: (:data:`AGENT_NAME_RE`), and no workspace route can ever serve it.
RUN_DATA_DIR = ".run-data"


def is_thread_slug(name: object) -> bool:
    """True when *name* is the :func:`instance_slug` of a plain thread id.

    A plain id (a UUID, as the chat sends) is its own readable part, so the
    digest of that part must match. A folder name that only looks like a
    slug fails the digest, so a member's own ``outputs/q3-20240101`` folder
    is never taken for a thread's.
    """
    if not isinstance(name, str):
        return False
    m = _THREAD_SLUG_RE.fullmatch(name)
    if m is None:
        return False
    digest = hashlib.sha256(m["readable"].encode("utf-8")).hexdigest()[:8]
    return digest == m["digest"]


def thread_slug(thread_id: object) -> str:
    """The folder name of a thread's own outputs: :func:`instance_slug` of its id.

    Raises ``ValueError`` for an id that :func:`is_thread_slug` cannot
    recognise again. That id gets no sandbox, so every thread folder on disk
    is one that the session routes can tell apart from any other folder.
    """
    raw = str(thread_id or "").strip()
    slug = instance_slug(raw) if raw else ""
    if not slug or not is_thread_slug(slug):
        raise ValueError(f"not a plain thread id: {raw[:80]!r}")
    return slug


def thread_outputs_rel(thread_id: object) -> str:
    """``outputs/<thread slug>``, relative to the working dir."""
    return f"outputs/{thread_slug(thread_id)}"


def upload_dir_rel(instance: object, thread_id: object) -> str:
    """Where a chat upload lands, relative to the workspace root (H-229).

    THE one rule. The upload route writes there, and ``read_attachment``
    reads there, so the two cannot disagree. It takes the thread folder from
    :func:`thread_slug`, the same slug as :func:`thread_outputs_rel`.

    * A shared agent's tenant dir (``o:<org>``) is one folder for every member
      of the organization. So an upload lands in ``inputs/<thread slug>/``,
      and ``read_attachment`` reads it only in a run of that thread (D12).
      The session file routes still serve another thread's folder (H-227).
    * Any other workspace keeps ``inputs/``. A personal agent's dir holds only
      its member's files.

    A tenant dir with an id that is not a plain thread id raises
    ``ValueError``, so the caller fails closed.
    """
    if is_tenant_instance(instance):
        return f"inputs/{thread_slug(thread_id)}"
    return "inputs"


def run_data_rel(organization_id: object, thread_id: object) -> str:
    """``.run-data/<org slug>/<thread slug>``, relative to :func:`state_root`."""
    org = str(organization_id or "").strip()
    if not org:
        raise ValueError("run data needs an organization id")
    return f"{RUN_DATA_DIR}/{instance_slug(org)}/{thread_slug(thread_id)}"


def is_other_thread_rel(rel: str, own_slug: str | None) -> bool:
    """True when *rel* lies in the output folder of a thread that is not *own_slug*.

    The ONE rule for "another chat's output folder" (§16.3). The session
    routes, ``write_artifact`` and ``save_note`` all ask it.
    """
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p not in ("", ".")]
    return (
        len(parts) >= 2 and parts[0] == "outputs"
        and is_thread_slug(parts[1]) and parts[1] != own_slug
    )


# ── The author of a skill (review P1, fix round 1) ───────────────────────────

#: The skills of a working dir, relative to it.
SKILLS_REL = "agent-data/skills"
#: A skill folder records the member who made it, in this file. A sandboxed
#: run loads, and runs the scripts of, only the skills of its OWN member, so
#: no member's skill text or code reaches another member's run. Every writer
#: of a working dir refuses to write this name, so only a host writer can make
#: it, and only for the member who first writes into the folder.
SKILL_AUTHOR_MARKER = ".metorite-author"


def skill_top_rel(rel: str) -> str | None:
    """``agent-data/skills/<top>`` for a path inside a skill folder, else ``None``."""
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if len(parts) >= 4 and parts[0] == "agent-data" and parts[1] == "skills":
        return f"{SKILLS_REL}/{parts[2]}"
    return None


def skill_author(workspace: Path, top_rel: str) -> str | None:
    """The member recorded for a skill folder, read with the safe opener, or ``None``."""
    from acb_skills import safe_open

    try:
        raw = safe_open.read_bytes(Path(workspace), f"{top_rel}/{SKILL_AUTHOR_MARKER}", limit=1024)
    except (safe_open.UnsafePath, OSError):
        return None
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace").strip().lower() or None


def refused_write(
    workspace: Path, rel: str, *, member: str | None, thread_id: str | None = None,
    own_slug: str | None = None,
) -> str | None:
    """Why a host writer may not write *rel* in a working dir, or ``None``.

    1. The author marker itself is reserved.
    2. The output folder of another thread is not this run's (§16.3). The own
       folder is *own_slug*, else the slug of *thread_id*.
    3. A skill folder that another member made is theirs alone.
    """
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if parts and parts[-1] == SKILL_AUTHOR_MARKER:
        return "that file name is reserved"
    own = own_slug or (instance_slug(str(thread_id)) if thread_id else None)
    if is_other_thread_rel(rel, own):
        return "that folder belongs to another chat"
    top = skill_top_rel(rel)
    if top is not None:
        author = skill_author(workspace, top)
        who = str(member or "").strip().lower()
        if author is not None and author != who:
            return "that skill belongs to another member"
    return None


def claim_skill(workspace: Path, rel: str, member: str | None) -> tuple[str, bytes] | None:
    """Record *member* as the author of the skill folder of *rel*, when none is.

    Returns ``(marker rel, bytes)`` when it wrote the marker, so the caller can
    mirror it to the blob store, else ``None``. Raises ``ValueError`` when
    there is no member, because a skill with no author never loads.
    """
    top = skill_top_rel(rel)
    if top is None or skill_author(workspace, top) is not None:
        return None
    who = str(member or "").strip().lower()
    if not who:
        raise ValueError("a skill needs a member who makes it")
    from acb_skills import safe_open

    marker = f"{top}/{SKILL_AUTHOR_MARKER}"
    data = who.encode("utf-8")
    try:
        safe_open.write_bytes(Path(workspace), marker, data, exclusive=True)
    except FileExistsError:
        return None
    return marker, data


def agent_code_dir(agent_name: str) -> Path:
    """The agent's source checkout — shared by every user of the agent.

    Equals ``{agents_clone_dir}/repos/{agent_name}``, which is what
    ``loader.load_agent`` always clones to and what
    ``gateway.routes.workspace._canonical_workspace_dir`` already returns.
    """
    return clone_root() / require_agent_name(agent_name)


def agent_state_dir(agent_name: str, instance: str = "") -> Path:
    """The working directory for one tenant of *agent_name*.

    ``instance=""`` returns :func:`agent_code_dir` unchanged — see the module
    docstring. Any other key returns a private directory that holds only the
    three durable folders (``agent-data``/``inputs``/``outputs``, i.e.
    ``blob_store.STORE_FOLDERS``) and never any source.

    Pure path arithmetic — use :func:`ensure_state_dir` when the directory
    should exist (it also stamps the instance marker).
    """
    if not instance:
        return agent_code_dir(agent_name)
    return state_root() / require_agent_name(agent_name) / instance_slug(instance)


def ensure_state_dir(agent_name: str, instance: str = "") -> Path:
    """:func:`agent_state_dir`, created and stamped with its instance marker.

    The marker is what lets :func:`workspace_blob_key` map the directory back
    to its partition later. For the shared key this is a no-op resolver call:
    the clone dir is the loader's concern and gets no marker.
    """
    d = agent_state_dir(agent_name, instance)
    if instance:
        d.mkdir(parents=True, exist_ok=True)
        marker = d / _INSTANCE_MARKER
        try:
            if not marker.exists():
                marker.write_text(instance, encoding="utf-8")
        except OSError:
            # The directory itself exists; blob writes fall back to the
            # slug-less key only if the marker also can't be read later.
            pass
    return d


def workspace_blob_key(workspace: Path | str) -> tuple[str, str]:
    """``(agent_name, instance)`` for any workspace directory.

    A clone dir's basename IS the agent name (the loader clones with
    ``clone_as=agent_name``); a state dir carries the agent name one level up
    and its instance in the marker. This is the single mapping the gateway's
    write-through mirrors use, so a file edited in the file manager lands in
    the SAME partition the run that created it used — regardless of who is
    looking at the directory.
    """
    p = Path(workspace)
    if p.parent.parent == state_root():
        agent = p.parent.name
        try:
            instance = (p / _INSTANCE_MARKER).read_text(encoding="utf-8").strip()
        except OSError:
            instance = ""
        return agent, instance
    return p.name, ""
