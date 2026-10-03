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
    "TENANT_INSTANCE_PREFIX",
    "InvalidAgentName",
    "agent_code_dir",
    "agent_state_dir",
    "clone_root",
    "ensure_state_dir",
    "instance_slug",
    "is_tenant_instance",
    "is_valid_agent_name",
    "require_agent_name",
    "state_root",
    "tenant_instance",
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


def thread_slug(thread_id: object) -> str:
    """The folder name of one chat thread: :func:`instance_slug` of its id.

    ``maf_coding_engine.md`` §16.3 names this form for the thread's outputs.
    H-229 uses it for the thread's uploads. An empty id raises ``ValueError``,
    so a caller with no thread fails closed.
    """
    tid = str(thread_id or "").strip()
    if not tid:
        raise ValueError("a thread folder needs a thread id")
    return instance_slug(tid)


def upload_dir_rel(instance: object, thread_id: object) -> str:
    """Where a chat upload lands, relative to the workspace root (H-229).

    THE one rule. The upload route writes there, and ``read_attachment``
    reads there, so the two cannot disagree.

    * A shared agent's tenant dir (``o:<org>``) is one folder for every member
      of the organization. So an upload lands in ``inputs/<thread slug>/``, and
      only a run of that thread reads it (D12).
    * Any other workspace keeps ``inputs/``. A personal agent's dir holds only
      its member's files.

    A tenant dir with no thread id raises ``ValueError``.
    """
    if is_tenant_instance(instance):
        return f"inputs/{thread_slug(thread_id)}"
    return "inputs"


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
