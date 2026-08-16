"""write_artifact — agent tool for writing files to a session workspace.

Auto-injected into every agent alongside ``web_search`` and ``call_agent``.

The tool:
1. Defaults to the ``outputs/`` directory when no visible workspace dir
   (``inputs/``, ``outputs/``, ``agent-data/``) is specified in the path.
2. Writes the file under ``{workspace_root}/{path}`` (creating parent dirs).
3. Computes a SHA-256 hash of the content.
4. Emits an AG-UI ``CUSTOM`` event ``artifact_created`` / ``artifact_updated``
   so the Control Plane sidebar updates in real time.
5. PATCHes the gateway to register the workspace root on the session.
6. Returns a ``download_url`` the agent SHOULD embed in its text response.

Usage by agents:
    result = await write_artifact("summary.md", "# Sales Summary\\n...")
    # File lands in outputs/summary.md (auto-prefixed)
    # Agent outputs: [📄 Download summary.md]({download_url})
"""
from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

_WRITE_ARTIFACT_CONTEXT: dict[str, str] = {}
"""
Thread/coroutine-local store keyed by session_id, set by the executor
before each agent run::

    _WRITE_ARTIFACT_CONTEXT["session_id"] = session_id
    _WRITE_ARTIFACT_CONTEXT["workspace_root"] = "/tmp/acb_agents/repos/agent-sales-assistant"
    _WRITE_ARTIFACT_CONTEXT["gateway_url"] = "http://127.0.0.1:8000"
    _WRITE_ARTIFACT_CONTEXT["gateway_token"] = "sk-local-dev-..."

The executor clears this after each run.
"""

# Visible workspace dirs — files written outside these are hidden in the UI.
_VISIBLE_DIRS = frozenset({"inputs", "outputs", "agent-data"})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _current_agent_name() -> str:
    """Best-effort agent name for the current run (blob-store key).

    Prefers the explicit context value the executor sets; falls back to the
    workspace_root basename ({agents_clone_dir}/repos/<agent_name>). A tenant
    state dir ({agents_clone_dir}/state/<agent_name>/<slug>) puts the SLUG in
    the basename — keying blobs by slug would silently shard one agent's
    store — so the agent name is its parent there.
    """
    name = _WRITE_ARTIFACT_CONTEXT.get("agent_name")
    if name:
        return str(name)
    root = _WRITE_ARTIFACT_CONTEXT.get("workspace_root")
    if not root:
        return ""
    p = Path(root)
    if p.parent.parent.name == "state":
        return p.parent.name
    return p.name


async def mirror_to_blob_store(
    rel_path: str,
    data: bytes,
    *,
    mime_type: str = "application/octet-stream",
    action: str = "modify",
    actor: str = "agent",
) -> None:
    """Write-through a workspace file into the authoritative blob store.

    Files under agent-data/, inputs/, outputs/ are mirrored to Postgres (source
    of truth; the disk workspace is a cache) and a version-history row recorded.
    No-op for other paths, when the store isn't available, or on any error — the
    on-disk file is already written, so this never blocks the agent.
    """
    try:
        from acb_memory import is_stored_path, put_file
    except ImportError:
        return
    if not is_stored_path(rel_path):
        return
    agent_name = _current_agent_name()
    if not agent_name:
        return
    await put_file(
        agent_name,
        rel_path.replace("\\", "/"),
        data,
        mime_type=mime_type,
        action=action,
        run_id=_WRITE_ARTIFACT_CONTEXT.get("run_id"),
        session_id=_WRITE_ARTIFACT_CONTEXT.get("session_id"),
        actor=actor,
        # The run's tenant partition, set by the executor alongside
        # workspace_root — disk and store must carry the SAME key, or a
        # personal agent's files rehydrate into the wrong person's run.
        instance=_WRITE_ARTIFACT_CONTEXT.get("instance", ""),
    )


def _normalise_path(path: str) -> str:
    """Strip leading slashes/dots and ensure the path lives in a visible dir.

    If *path* doesn't start with ``inputs/``, ``outputs/``, or ``agent-data/``,
    it is automatically prefixed with ``outputs/`` so the file appears in the
    Files Viewer sidebar.

    NOTE: this only strips a LEADING ``/.`` — it does not neutralise an EMBEDDED
    ``..`` (e.g. ``outputs/../../etc/x``). Containment is enforced separately by
    :func:`resolve_in_workspace`; every tool that turns a caller path into a
    filesystem path MUST route it through that guard.
    """
    clean = path.replace("\\", "/").lstrip("/.")
    # Already in a visible dir — use as-is.
    for d in _VISIBLE_DIRS:
        if clean == d or clean.startswith(d + "/"):
            return clean
    # Default: write to outputs/
    return f"outputs/{clean}"


def resolve_in_workspace(root: str | Path, rel: str) -> Path | None:
    """Resolve *rel* under *root*, returning it ONLY if it stays inside the root.

    The single path-containment guard for every workspace read/write tool
    (``write_artifact``, ``save_note``, ``recall_notes``, …). Returns ``None`` on
    any traversal escape — an embedded ``..`` that climbs out, or an absolute
    path that resolves outside the workspace — so callers fail closed instead of
    reading/writing arbitrary files. Symlinks are resolved on both sides so a
    symlinked escape is caught too.
    """
    root_r = Path(root).resolve()
    # strict=False (the default): non-existent leaves still resolve lexically,
    # so a not-yet-created target is contained-checked correctly.
    target = (root_r / rel).resolve()
    try:
        target.relative_to(root_r)
    except ValueError:
        return None
    return target


async def write_artifact(
    path: str,
    content: str | bytes,
    *,
    encoding: str | None = "utf-8",
    overwrite: bool = False,
) -> dict:
    """Write a file to the agent's workspace and surface it in the UI file browser.

    Call this any time you generate a document, report, script, spreadsheet,
    PDF, image, or any other file that the operator should be able to view or
    download from the Control Plane.

    Files are automatically placed in ``outputs/`` unless you specify
    ``inputs/`` (user-provided files) or ``agent-data/`` (reference data).

    After calling this, **embed the returned ``download_url`` in your text
    response** so the operator can click to download.

    Args:
        path:     Relative file path, e.g. ``"summary.md"`` or
                  ``"reports/q2_summary.md"``.  If the path does not start
                  with ``inputs/``, ``outputs/``, or ``agent-data/``, it is
                  automatically placed in ``outputs/``.
                  Parent directories are created automatically.
        content:  File content — either a ``str`` (written with *encoding*)
                  or ``bytes`` (written as-is; set *encoding* to ``None``).
        encoding: Text encoding for ``str`` content.  Default ``"utf-8"``.
                  Pass ``None`` when *content* is already ``bytes``.
        overwrite: By default (``False``) an existing file is **never**
                  clobbered — the new file is written to a uniquified name
                  (``report (1).md``) so originals/user uploads are preserved.
                  Set ``True`` to deliberately replace the file in place.

    Returns:
        ``{"path": str, "size": int, "sha256": str, "download_url": str}``

        ``path``/``download_url`` reflect the file *actually* written (which may
        be a uniquified name if a file already existed and ``overwrite`` is off).
        *download_url* is a relative URL suitable for a clickable markdown
        link, e.g. ``/api/agent/workspace/{session}/file?path=outputs/x.md``.
    """
    import asyncio

    workspace_root = _WRITE_ARTIFACT_CONTEXT.get("workspace_root")
    session_id = _WRITE_ARTIFACT_CONTEXT.get("session_id")
    gateway_url = _WRITE_ARTIFACT_CONTEXT.get("gateway_url", "http://127.0.0.1:8000")
    gateway_token = _WRITE_ARTIFACT_CONTEXT.get("gateway_token", "sk-local-dev-change-me")

    if not workspace_root:
        # Fallback: write to a temp dir per session
        import tempfile
        workspace_root = str(Path(tempfile.gettempdir()) / "acb_artifacts" / (session_id or "unknown"))
        _WRITE_ARTIFACT_CONTEXT["workspace_root"] = workspace_root

    root = Path(workspace_root)
    root_r = root.resolve()
    # Normalise path and auto-prefix with outputs/ if needed
    clean_path = _normalise_path(path)
    # Containment guard: refuse any path that escapes the workspace (embedded
    # ``..`` or an absolute path resolving outside root). Fail closed.
    target = resolve_in_workspace(root, clean_path)
    if target is None:
        return {"error": f"Path '{path}' escapes the workspace and was refused."}
    clean_path = target.relative_to(root_r).as_posix()

    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    # Non-destructive by default: never clobber an existing file (a user upload
    # in inputs/, or a previously generated artifact). Uniquify to "name (1).ext"
    # — the same collision policy the upload endpoint uses. Pass overwrite=True to
    # deliberately replace the file in place.
    if target.exists() and not overwrite:
        stem, ext = target.stem, target.suffix
        counter = 1
        while target.exists():
            target = target.parent / f"{stem} ({counter}){ext}"
            counter += 1
        clean_path = target.relative_to(root_r).as_posix()

    # Write file
    if isinstance(content, str):
        data = content.encode(encoding or "utf-8")
    else:
        data = bytes(content)

    _existed = target.exists()
    target.write_bytes(data)
    digest = _sha256(data)
    size = len(data)

    mime, _ = mimetypes.guess_type(target.name)
    mime = mime or "application/octet-stream"

    # Write-through to the authoritative blob store (agent-data/inputs/outputs).
    # Fire-and-forget: the disk file is already written, so a store outage never
    # blocks the agent.
    import asyncio as _asyncio
    _asyncio.ensure_future(mirror_to_blob_store(
        clean_path, data, mime_type=mime,
        action="modify" if (_existed and overwrite) else "create",
    ))

    # Build download URL (relative path — works from the frontend chat UI).
    download_url = (
        f"/api/agent/workspace/{session_id}/file"
        f"?path={clean_path}"
    ) if session_id else None

    # Build artifact entry
    artifact = {
        "path": clean_path,
        "name": target.name,
        "size": size,
        "sha256": digest,
        "mime_type": mime,
        "modified_at": datetime.now(tz=UTC).isoformat(),
        "is_dir": False,
    }

    # Fire-and-forget: emit AG-UI CUSTOM event into the active SSE stream
    # (via _active_run_queue context var set by the executor) and also
    # register the workspace path on the session via the gateway.
    asyncio.ensure_future(_notify(
        session_id=session_id,
        workspace_root=workspace_root,
        artifact=artifact,
        gateway_url=gateway_url,
        gateway_token=gateway_token,
    ))

    result: dict = {"path": clean_path, "size": size, "sha256": digest}
    if download_url:
        result["download_url"] = download_url
    # Advisory lint for HTML documents. The sandbox fails silently (a CDN link is
    # just blocked, a typo'd cc- class just renders unstyled), so surface those
    # mistakes here while the agent can still fix them. Never blocks the write.
    _suffix = target.suffix.lower()
    if _suffix in {".html", ".htm", ".jsx", ".tsx"} and isinstance(content, str):
        from acb_skills.artifact_lint import (  # noqa: PLC0415
            lint_artifact_html,
            lint_artifact_source,
        )

        # JSX is not HTML — running the document linter on it yields only noise.
        # But the CSP failure is shared, and it is silent, so React artifacts get
        # the narrow remote-asset check.
        warnings = (
            lint_artifact_html(content, full_page=True)
            if _suffix in {".html", ".htm"}
            else lint_artifact_source(content)
        )
        if warnings:
            result["warnings"] = warnings
            result["warning_note"] = (
                "The artifact was saved, but these issues will degrade how it "
                "renders. Fix them and write the file again with overwrite=True."
            )
    return result


async def share_artifact(path: str) -> dict:
    """Surface a file you ALREADY created as a downloadable, previewable card in
    the chat — and get back a download link.

    Use this whenever you produced a file with your own tools (shell, editor,
    Write, a script you ran) instead of ``write_artifact``.  Do NOT re-create or
    re-read the file's contents — just point this tool at the path you already
    wrote and it will appear in the chat with a Download button and an inline
    preview, with zero extra effort on your part.  You do not need to construct
    any URL by hand; the returned ``download_url`` is the canonical link.

    Pass a single file, or a directory to share every file inside it.

    Args:
        path: File or directory path relative to your workspace (e.g.
              ``"outputs/report.pdf"``, ``"q2_summary.xlsx"``, or ``"outputs"``
              to share the whole folder).  Absolute paths inside the workspace
              are also accepted.

    Returns:
        ``{"artifacts": [{"path","name","size","mime_type","download_url"}, ...],
        "download_url": <first file's link>}``.  On error,
        ``{"error": str, "artifacts": []}``.
    """
    import asyncio

    workspace_root = _WRITE_ARTIFACT_CONTEXT.get("workspace_root")
    session_id = _WRITE_ARTIFACT_CONTEXT.get("session_id")
    gateway_url = _WRITE_ARTIFACT_CONTEXT.get("gateway_url", "http://127.0.0.1:8000")
    gateway_token = _WRITE_ARTIFACT_CONTEXT.get("gateway_token", "sk-local-dev-change-me")

    if not workspace_root:
        return {"error": "No workspace is configured for this run.", "artifacts": []}

    root = Path(workspace_root).resolve()
    raw = (path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        return {"error": "A file or directory path is required.", "artifacts": []}
    candidate = Path(raw)
    target = (candidate if candidate.is_absolute() else root / raw).resolve()

    # Path-traversal guard — the target must stay inside the workspace root.
    try:
        target.relative_to(root)
    except ValueError:
        return {"error": f"Path '{path}' is outside the workspace.", "artifacts": []}
    if not target.exists():
        return {"error": f"File not found: {path}", "artifacts": []}

    # Collect the file(s) to share (a directory shares everything within it).
    files: list[Path] = []
    if target.is_dir():
        for p in sorted(target.rglob("*")):
            if p.is_file():
                files.append(p)
                if len(files) >= 50:
                    break
    else:
        files = [target]
    if not files:
        return {"error": f"No files found at: {path}", "artifacts": []}

    artifacts: list[dict] = []
    for f in files:
        rel = f.resolve().relative_to(root).as_posix()
        size = f.stat().st_size
        mime, _ = mimetypes.guess_type(f.name)
        mime = mime or "application/octet-stream"
        download_url = (
            f"/api/agent/workspace/{session_id}/file?path={rel}"
            if session_id else None
        )
        art = {
            "path": rel,
            "name": f.name,
            "size": size,
            "mime_type": mime,
            "modified_at": datetime.now(tz=UTC).isoformat(),
            "is_dir": False,
        }
        # Same CUSTOM event write_artifact emits → renders the ArtifactCard.
        asyncio.ensure_future(_notify(
            session_id=session_id,
            workspace_root=workspace_root,
            artifact=art,
            gateway_url=gateway_url,
            gateway_token=gateway_token,
        ))
        entry: dict = {"path": rel, "name": f.name, "size": size, "mime_type": mime}
        if download_url:
            entry["download_url"] = download_url
        artifacts.append(entry)

    result: dict = {"artifacts": artifacts}
    if artifacts and artifacts[0].get("download_url"):
        result["download_url"] = artifacts[0]["download_url"]
    return result


async def _notify(
    *,
    session_id: str | None,
    workspace_root: str,
    artifact: dict,
    gateway_url: str,
    gateway_token: str,
) -> None:
    """Background task: push CUSTOM SSE event into the active run queue AND
    register workspace on the gateway.  Non-fatal — all errors are swallowed.
    """
    # 1. Push AG-UI CUSTOM event into the active executor SSE queue so the
    #    frontend receives it immediately as part of the existing chat stream.
    #    resolve_run_queue falls back to the plain _RUN_QUEUES registry (keyed
    #    by session_id) so this reaches the chat stream even for Copilot-SDK
    #    tools whose fresh-context thread can't see the ContextVar.
    try:
        from orchestrator.executor import resolve_run_queue
        queue = resolve_run_queue(session_id)
        if queue is not None:
            _artifact_data = {
                "path": artifact["path"],
                "sha256": artifact.get("sha256"),
                "size": artifact.get("size"),
                "mime_type": artifact.get("mime_type"),
            }
            await queue.put({
                "type": "CUSTOM",
                "name": "artifact_created",
                "value": _artifact_data,
            })
    except Exception:
        pass

    if not session_id:
        return

    # 2. Also POST to gateway events endpoint so any other SSE subscribers
    #    (future browser tabs, monitoring) receive it.
    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {gateway_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=5) as client:
            # Register workspace root on the session (idempotent PATCH)
            await client.patch(
                f"{gateway_url}/agent/workspace/{session_id}",
                json={"workspace_path": workspace_root},
                headers=headers,
            )
            # Emit to gateway subscriber queues
            await client.post(
                f"{gateway_url}/agent/workspace/{session_id}/events",
                json={
                    "name": "artifact_created",
                    "path": artifact["path"],
                    "sha256": artifact.get("sha256"),
                    "size": artifact.get("size"),
                },
                headers=headers,
            )
    except Exception:
        pass  # Non-fatal — the sidebar can always refresh manually


# ── Generative UI ───────────────────────────────────────────────────────────

# The component types the frontend GenerativeUINode renderer whitelists. Kept in
# sync with GenerativeUINode.tsx KNOWN_TYPES so the tool's docstring can steer
# the model toward valid trees (and we can reject obviously-wrong ones early).
_GEN_UI_TYPES = {
    "card", "stack", "row", "heading", "text", "markdown", "badge",
    "divider", "keyValue", "table", "list", "code", "link", "button", "callout",
    "template", "html", "react", "icon",
}


def _warn_fields(warnings: list[str]) -> dict:
    """Lint warnings as result fields — empty dict when the markup is clean."""
    if not warnings:
        return {}
    return {
        "warnings": warnings,
        "warning_note": (
            "The card was rendered, but these issues will degrade how it looks. "
            "Fix them and emit it again."
        ),
    }


async def emit_generative_ui(ui: str) -> dict:
    """Render a rich, interactive, animated UI element inline in the chat, on the fly.

    REACH FOR THIS EAGERLY — a well-made UI card beats a paragraph almost every
    time the answer is data, a status, a comparison, a metric, a choice, or a
    value the user should set. Default to rendering UI whenever:
      • you're reporting numbers/metrics/KPIs → statDashboard or barChart
      • you're describing current state/conditions → weatherCard or a card
      • you're comparing options → comparison
      • you're showing progress/steps/a checklist → progressTracker
      • the user must PICK or SET something → buttons, or a custom-HTML card with
        a slider / input / select that submits their choice back (see mode 3).
    Whenever there's a genuine chance to let the user interact — adjust a value,
    pick an option, confirm a choice — prefer an interactive UI over asking in
    prose. Do NOT be trivial about it: a one-line factual reply ("yes", "it's
    42") or a long narrative explanation should stay as text. Use UI when it
    genuinely clarifies or when interaction is useful — not as decoration.

    A card you emit is PART OF THE TRANSCRIPT — it is persisted with the message
    and re-renders on later turns, on reload, and on any device. It does not
    expire and later messages do not close it. Never tell the user inline UI is
    temporary or offer a file as "something more durable"; if a card is missing
    from an earlier turn, that is a bug to report, not expected behaviour.

    All three modes follow the Metorite design language automatically
    (blue primary, warm-orange accent, rounded cards, subtle motion). Templates
    and the component tree are on-brand by construction; custom HTML inherits the
    real design tokens as CSS variables (see mode 3), so lean on those.

    ``ui`` is a JSON object (string or dict). Two OPTIONAL top-level fields
    apply to every mode:

    • ``"surface"`` — ``"inline"`` (default; a card in the chat transcript) or
      ``"panel"`` — opens as an IMMERSIVE view in the side panel (like a
      document), with a compact "open" chip in the transcript. Use ``panel``
      for big/rich UI: full dashboards, detailed itineraries, long recipes,
      multi-section forms. Inline cards should stay compact.
    • ``"hitl": true`` — BLOCKING human-in-the-loop: this tool call PAUSES the
      run until the user interacts (submits the form / clicks an option /
      presses a button), and returns their values as this call's result
      (``{"ok":true,"response":<their answer>}``). Use whenever you need the
      user's input to continue — a form to fill, an option to pick, a value to
      set. Without it, clicks arrive as a NEW chat message instead.

    It supports FOUR modes; prefer them in this order
    (template → tree → react → html):

    1. NAMED TEMPLATE — pre-designed, animated, on-brand components. You supply
       ONLY data; the design is fixed and looks great every time. Use first when
       one fits. Shape: ``{"type":"template","props":{"name":<t>,"data":{...}}}``.
       Available templates and their ``data`` shapes:
         • weatherCard — {location, tempC|tempF, condition('sunny'|'cloudy'|
             'rain'|'snow'|'storm'), highC?, lowC?, humidity?, wind?,
             forecast?:[{day,condition,high,low}]}
         • statDashboard — {title?, stats:[{label, value, unit?, delta?:number}]}
         • barChart — {title?, unit?, bars:[{label, value,
             tone?('primary'|'success'|'warning'|'danger')}]}
         • sparkTrend — {label, value, unit?, delta?:number, series:number[]}
         • comparison — {title?, options:[{name, recommended?:bool,
             rows:[{label, value}]}]}
         • progressTracker — {title?, steps:[{label,
             state('done'|'active'|'pending')}]}
         • recipeCard — {title, description?, servings?, prepMinutes?,
             cookMinutes?, calories?, ingredients:[{item, amount?}],
             steps:[string], tags?:[string], tip?}
         • flightStatus — {airline?, flightNo, status('scheduled'|'boarding'|
             'departed'|'in-air'|'landed'|'delayed'|'cancelled'),
             from:{code,city?,time?,terminal?,gate?},
             to:{code,city?,time?,terminal?,gate?}, progressPct?, durationMin?,
             date?, note?}
         • trainStatus — {operator?, trainNo?, line?, status('scheduled'|
             'boarding'|'departed'|'arrived'|'delayed'|'cancelled'),
             from:{station,time?,platform?}, to:{station,time?,platform?},
             stops?:[{station, time?, state?('done'|'active'|'pending')}],
             delayMin?, note?}
         • formCard — {title?, description?, submitLabel?, fields:[{name,
             label, type('text'|'number'|'select'|'slider'|'toggle'|'date'|
             'textarea'), placeholder?, value?, required?, options?:[string]
             (select), min?/max?/step?/unit? (number|slider)}]} — a
             schema-driven form; PAIR WITH ``"hitl":true`` so the submitted
             values come back as this call's result. Replaces hand-written
             HTML forms.
         • optionPicker — {title?, description?, multi?:bool, options:[{id,
             label, description?, icon?, badge?, recommended?:bool}]} — rich
             choice cards for decisions; PAIR WITH ``"hitl":true``.

    2. COMPONENT TREE — a safe whitelist of typed primitives (data, not code).
       Each node is ``{"type":<kind>,"props":{...},"children":[...]}``. Kinds:
         card{title?} · stack · row · heading{text} · text{text,muted?} ·
         markdown{text} · badge{text,tone?} · divider · callout{title?,text?,tone?}
         keyValue{pairs:[{key,value}]} ·
         table{columns:["Deal","Amount"],rows:[["OsteoForge","₹8.4L"]]} —
             ``columns`` are HEADER STRINGS and each row is a list of cell
             values in the same order (objects like {key,label} / row dicts also
             render, but plain strings + positional rows are the shape to emit) ·
         list{items:[..],ordered?} · code{text} · link{href,text?} ·
         button{label,action,tone?} ·
         icon{name,size?,tone?,label?}
       ``icon`` renders any Lucide icon by ``name`` (kebab or Pascal, e.g.
       ``"cloud-sun"``, ``"CheckCircle"``, ``"trending-up"``) — on-brand, bundled,
       no network; unknown names fall back to a neutral glyph. Put an ``icon`` in
       a ``row`` beside ``text`` for labelled rows. ``tone`` ∈ success|error|
       warning|info|neutral (badges/callouts/icons) or primary|danger|default
       (buttons). A ``button``'s ``action`` string is sent back as the user's
       next message when clicked.

    3. REACT COMPONENT — a real React component for anything genuinely
       INTERACTIVE or stateful: multi-step forms, filterable/sortable tables,
       calculators, live-editable dashboards, small tools. Shape:
       ``{"type":"react","props":{"code":"<your component source>"}}``.

       Write ordinary modern React and DEFAULT-EXPORT the component
       (``export default function Dashboard() { … }``).

       • Hooks all work (useState/useEffect/useMemo/useReducer/useRef/context).
       • JSX and TypeScript syntax are both fine — it is compiled for you.
       • PREFER the prebuilt components: ``import { Report, Stat, Bars } from
         "@cc/ui"``. Call ``load_artifact_kit()`` for the list and
         ``load_artifact_kit("Stat,Bars")`` for their props. They are on-brand by
         construction and far cheaper than hand-writing the markup.
       • You may import ONLY from ``@cc/ui``, ``react``, and ``react-dom/client``.
         There is NO network in the sandbox, so no npm packages, no CDNs, no icon
         libraries. Inline any helpers and seed the data in the file.
       • Anything the kit doesn't cover: fall back to the same ``cc-*`` classes
         and ``--cc-*`` tokens as mode 4.
       • Talk back to the agent with ``window.ccSubmit("Label", value)`` (send a
         value the user set) or ``window.ccAction("message")`` (fire a fixed
         follow-up). Both are available from first mount.
       • Optional ``props.height`` (px); omit to auto-size.

       If it compiles but the build fails, the tool result carries the compiler
       errors — fix them and emit again.

    4. CUSTOM HTML — the escape hatch for bespoke animation/layout or genuinely
       interactive controls no template, tree, or React component covers. Shape:
       ``{"type":"html","props":{"code":"<div>…</div>"}}``. Your HTML/CSS/JS runs
       in an ISOLATED sandbox (its own opaque origin): it cannot reach the app,
       cookies, or the network, so inline everything — NO external CDNs, fonts, or
       images (use data: URIs). Optional ``props.height`` (px); omit to auto-size.

       DESIGN — follow the Metorite look. The frame pre-defines CSS
       variables from the app's real design tokens; USE THEM instead of
       hard-coding colors so your UI matches the product:
         --cc-primary (blue) · --cc-accent (warm orange) · --cc-fg · --cc-muted
         · --cc-card · --cc-secondary · --cc-border · --cc-success · --cc-warning
         · --cc-danger · --cc-radius (0.75rem) · --cc-ease (motion curve).
       Native ``<button>``, ``<input>``, ``<select>``, ``<textarea>`` and
       ``input[type=range]`` are already styled on-brand (add class ``cc-primary``
       to a button for the filled blue variant; ``cc-card`` for a panel). Prefer
       rem spacing, rounded corners (var(--cc-radius)), and subtle transitions
       (0.2s var(--cc-ease)). Keep it clean and professional — not flashy.

       REPORT DESIGN KIT — for a substantial DOCUMENT (analysis, plan, comparison,
       briefing) prefer writing it to an ``.html`` file with ``write_artifact`` so
       it opens full-page in the side panel, wrapped in ``<div class="cc-report">``.
       Call ``load_design_system("blocks")`` for the pre-styled block reference
       (callouts, grids, comparison tables, step lists, …) instead of hand-rolling
       report styling.

       INTERACTIVITY — two channels back to the agent:
         • ``data-cc-action="<message>"`` on a clickable element (or
           ``ccAction("…")`` in script) fires a FIXED follow-up message — like a
           button. Use for "Tell me more" / "Roll back" style actions.
         • ``data-cc-submit="<label>"`` on a button harvests every named control
           (``<input name=…>`` / select / textarea) in its enclosing ``<form>`` or
           ``[data-cc-form]`` and submits their VALUES back as the user's next
           message. Or call ``ccSubmit("Temperature", 22)`` /
           ``ccSubmit({temp:22,unit:"C"})`` directly. Use this whenever the user
           SETS a value — a slider, a number, a picked option — so the agent
           actually receives what they chose. This is the key to real two-way UI.

    Returns ``{"ok": true}`` on emit. Additive — also say in prose what you're
    showing. Keep template/tree/html discriminated by the top-level ``type``.

    Example (template — the preferred mode)::

        await emit_generative_ui('{"type":"template","props":{"name":'
          '"statDashboard","data":{"title":"Q3","stats":[{"label":"Revenue",'
          '"value":18,"unit":"%","delta":12}]}}}')
    """
    import json

    try:
        spec = json.loads(ui) if isinstance(ui, str) else ui
    except (json.JSONDecodeError, TypeError) as exc:
        return {"ok": False, "error": f"ui must be valid JSON: {exc}"}
    if not isinstance(spec, dict):
        return {"ok": False, "error": "ui must be a JSON object (a component node)"}

    # Advisory lint for the custom-HTML tier — the sandbox swallows these errors
    # silently, so report them back with the emit result. Inline cards are not
    # expected to carry the cc-report wrapper (that is for full-page documents).
    _ui_warnings: list[str] = []
    if spec.get("type") == "html":
        _code = (spec.get("props") or {}).get("code")
        if isinstance(_code, str):
            from acb_skills.artifact_lint import lint_artifact_html

            _ui_warnings = lint_artifact_html(_code, full_page=False)

    # ── HITL blocking mode (generative_ui_2 Phase 1) ──────────────────────
    # ``"hitl": true`` parks THIS tool call on the same Future machinery as
    # ask_questions: the UI's submit/action resolves it via
    # /agent/respond-input and the run resumes in the SAME turn with the
    # user's values as this tool's result. Without it, genUI submits arrive
    # as a NEW chat message (non-blocking).
    _blocking = bool(spec.pop("hitl", False))
    _request_id: str | None = None
    _fut = None
    if _blocking:
        try:
            import asyncio as _asyncio
            import uuid as _uuid

            from orchestrator.executor import _pending_user_input
            _request_id = _uuid.uuid4().hex
            _fut = _asyncio.get_running_loop().create_future()
            _pending_user_input[_request_id] = _fut
            spec["request_id"] = _request_id
        except Exception:
            _blocking, _request_id, _fut = False, None, None

    # Push the CUSTOM event into the active run's SSE queue. resolve_run_queue
    # tries the ContextVar (native-MAF) first, then the plain _RUN_QUEUES
    # registry keyed by the session id — the latter is what makes this work for
    # GitHub-Copilot-SDK agents, whose tool callables run in a JSON-RPC read
    # thread with a fresh context where the ContextVar is invisible.
    try:
        from orchestrator.executor import resolve_run_queue
        session_id = _WRITE_ARTIFACT_CONTEXT.get("session_id")
        queue = resolve_run_queue(session_id)
        if queue is None:
            if _request_id is not None:
                from orchestrator.executor import _pending_user_input
                _pending_user_input.pop(_request_id, None)
            return {"ok": False, "error": "no active run stream to render into"}
        await queue.put({
            "type": "CUSTOM",
            "name": "generative_ui",
            "value": spec,
        })
        if not _blocking or _fut is None:
            return {"ok": True, **_warn_fields(_ui_warnings)}
        # Park until the user interacts (heartbeats the relay so the run
        # stays visibly alive — same wait as every other HITL surface).
        try:
            from orchestrator.executor import (
                _pending_user_input,
                wait_user_future,
            )
            try:
                _result = await wait_user_future(_fut, 3600)
            finally:
                _pending_user_input.pop(_request_id, None)
            return {
                "ok": True,
                "response": _result.get("answer", ""),
                **_warn_fields(_ui_warnings),
            }
        except Exception:
            return {
                "ok": True,
                "response": None,
                "note": "user did not respond to the UI",
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "no active run stream to render into"}
