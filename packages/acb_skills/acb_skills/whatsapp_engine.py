"""The chart engine of the WhatsApp channel (WS-47 WAC-10f).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.8.

**One chart language for the product.** The web app's
``src/lib/charts/kinds.mjs`` turns a short chart spec into a full ECharts
option, in Metorite's own look. The web chat draws it live. For WhatsApp,
this module runs ``src/lib/charts/render.mjs`` as a child process, which
draws the SAME chart to a PNG (ECharts SVG, then resvg with Geist).

**No port and no token.** The child reads JSON on stdin and writes JSON on
stdout. It reaches no network and reads no secret, so it adds no door to the
box. It runs as the gateway's own user, under the gateway's own sandbox.

**Bounded.** At most :data:`MAX_PARALLEL` children run at once, each one for
at most :data:`TIMEOUT_S`, and the reply is read up to :data:`MAX_OUTPUT`.

**It fails soft.** :class:`EngineUnavailable` means the engine is off, Node or
the file is missing, or the child broke: the caller falls back to the old
renderer, or answers in text. :class:`render.RenderError` carries the
engine's reason for a bad spec, so the model can fix the data.

Fence: ``tests/unit/test_wac_chart_engine.py``.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from acb_skills import whatsapp_render as render

#: ``packages/acb_skills/acb_skills`` → the repo root → the web app's renderer.
ENGINE = (Path(__file__).resolve().parents[3]
          / "workbench" / "control_plane" / "src" / "lib" / "charts" / "render.mjs")
#: The chart kinds of ``kinds.mjs``. ``test_wac_chart_engine`` holds the two
#: lists equal, so the tool never offers a kind the engine cannot draw.
KINDS = ("bar", "line", "area", "donut", "progress", "scatter", "heatmap", "radar",
         "box", "waterfall", "funnel", "calendar", "gantt")
TIMEOUT_S = 20
MAX_PARALLEL = 2
MAX_OUTPUT = 24 * 1024 * 1024
_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)


class EngineUnavailable(RuntimeError):
    """The engine cannot draw now. The caller falls back."""


def enabled() -> bool:
    """True when ``WHATSAPP_CHART_ENGINE`` is on (default off: ship dark)."""
    try:
        from acb_common import get_settings

        return bool(getattr(get_settings(), "whatsapp_chart_engine", False))
    except Exception:  # no settings: a test, or a script
        return False


def _node() -> str | None:
    try:
        from acb_common import get_settings

        name = str(getattr(get_settings(), "whatsapp_chart_node", "") or "node")
    except Exception:
        name = "node"
    return shutil.which(name)


def render_png(spec: dict[str, Any], *, mode: str = "dark") -> bytes:
    """One chart as a PNG. Raises :class:`render.RenderError` for a bad spec,
    and :class:`EngineUnavailable` when the engine cannot draw."""
    (out,) = render_many([spec], mode=mode)
    if isinstance(out, str):
        raise render.RenderError(out)
    return out


def render_many(specs: list[dict[str, Any]], *, mode: str = "dark") -> list[bytes | str]:
    """Each chart as PNG bytes, or the engine's reason it refused that one."""
    node = _node()
    if node is None or not ENGINE.is_file():
        raise EngineUnavailable("node or the chart engine is missing")
    payload = json.dumps({"charts": specs, "mode": mode}, ensure_ascii=False).encode("utf-8")
    if not _SLOTS.acquire(timeout=TIMEOUT_S):
        raise EngineUnavailable("the chart engine is busy")
    try:
        # A fixed argv: our node binary and our own file. No shell, no input in it.
        proc = subprocess.run(
            [node, str(ENGINE)], input=payload, capture_output=True,
            timeout=TIMEOUT_S, cwd=str(ENGINE.parent), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineUnavailable(f"the chart engine did not answer ({type(exc).__name__})") from exc
    finally:
        _SLOTS.release()
    if proc.returncode != 0 or len(proc.stdout) > MAX_OUTPUT:
        raise EngineUnavailable(f"the chart engine failed (exit {proc.returncode})")
    try:
        images = json.loads(proc.stdout.decode("utf-8"))["images"]
    except (ValueError, KeyError, TypeError) as exc:
        raise EngineUnavailable("the chart engine answered in a wrong shape") from exc
    if not isinstance(images, list) or len(images) != len(specs):
        raise EngineUnavailable("the chart engine answered in a wrong shape")
    out: list[bytes | str] = []
    for item in images:
        if isinstance(item, dict) and isinstance(item.get("png"), str):
            png = base64.b64decode(item["png"])
            if not png.startswith(b"\x89PNG"):
                raise EngineUnavailable("the chart engine sent a file that is not a PNG")
            out.append(png)
        elif isinstance(item, dict) and isinstance(item.get("error"), str):
            out.append(item["error"][:300])
        else:
            raise EngineUnavailable("the chart engine answered in a wrong shape")
    return out
