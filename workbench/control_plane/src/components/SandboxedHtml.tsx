"use client";

/**
 * SandboxedHtml — runs agent-GENERATED HTML/CSS/JS in a locked-down iframe.
 *
 * This is Tier 3 of generative UI (see GenerativeUINode): the escape hatch for
 * when no declarative primitive (Tier 1) or named template (Tier 2) fits and the
 * agent genuinely needs custom, animated, reactive markup it wrote on the fly.
 *
 * ── Trust model ────────────────────────────────────────────────────────────
 * The agent-authored code is NEVER trusted. It runs inside an <iframe> whose
 * `sandbox` attribute grants ONLY `allow-scripts` — deliberately WITHOUT
 * `allow-same-origin`. Consequences, all intentional:
 *   • the frame gets an opaque, unique origin → it cannot read our cookies,
 *     localStorage, IndexedDB, or reach any same-origin API;
 *   • it cannot touch the parent DOM (no window.parent.document access);
 *   • a strict CSP inside the srcdoc blocks network egress (no fetch/img/script
 *     to remote hosts) so generated code can't exfiltrate or beacon out;
 *   • navigation/top-level redirects are not granted.
 * The ONLY channel back to the app is `postMessage`, which we validate and map
 * onto the existing onAction(...) follow-up-message contract — the same contract
 * declarative buttons use. Two shapes are bridged: `ccAction("msg")` fires a
 * fixed follow-up (like a button), and `ccSubmit(label, value)` reports a VALUE
 * the user set (slider/text/select) as a structured follow-up — so a Tier-3 card
 * can be genuinely interactive (collect input), not just clickable, with none of
 * the ambient authority.
 *
 * ── Icons without a network ────────────────────────────────────────────────
 * The CSP blocks remote images by design, so icons can't be pulled from a CDN.
 * Instead the parent pre-resolves the Lucide icons the agent asked for into
 * inline SVG STRINGS (buildIconMap) and injects them into the frame, exposed as
 * `ccIcon("Name")` and auto-filled into `[data-cc-icon]` placeholders. Icons are
 * data (SVG), so this preserves the no-network guarantee.
 *
 * ── React inside the frame ─────────────────────────────────────────────────
 * The frame is self-contained: it cannot import from our bundle. If the agent
 * wants React it must inline it. To keep generated code both capable and offline
 * (CSP blocks CDNs), we do NOT ship React into the frame; agents author plain
 * HTML/CSS/JS (which can be arbitrarily animated/reactive via the DOM + CSS).
 * "React elements" in the product sense are served by Tier 2 templates, which
 * ARE real React components in our bundle. This is the safe division of labour:
 * our React (trusted, templated) vs. their DOM/JS (untrusted, sandboxed).
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { buildIconMap } from "@/lib/iconSvg";
import { appTokenMap } from "@/lib/theme/app-tokens";
import { BRIDGE, buildSrcDoc } from "@/lib/theme/sandbox-frame";
import { THEME } from "@/lib/theme/themes";
import { useMode } from "@/lib/theme/surfaces";

interface SandboxedHtmlProps {
  /** Agent-authored HTML. May contain <style> and <script>. No external hosts. */
  html: string;
  /** Optional fixed height (px). Omit to auto-size to content via postMessage. */
  height?: number;
  /** Button/interaction actions bubble up here as follow-up messages. */
  onAction?: (action: string) => void;
  /** Lucide icon NAMES the content references. Resolved to inline SVG here —
   *  by the active theme's pack — and injected so generated code can drop them
   *  via ccIcon("name") or [data-cc-icon], with no network.
   *
   *  Names rather than a pre-rendered map on purpose: the pack is a theme
   *  choice, so resolving in the caller froze every sandboxed app on Lucide. */
  iconNames?: unknown;
  /** Chrome-less, fill-height mode for full-page reports in the side panel
   *  (no "Generated UI / sandboxed" header, no card frame, fills its container).
   *  Inline chat cards keep the default framed chrome. */
  chromeless?: boolean;
}

function describeSubmit(payload: unknown): string {
  const kv = (o: Record<string, unknown>): string =>
    Object.entries(o)
      .map(([k, v]) => `${k}: ${String(v)}`)
      .join(", ");
  if (payload == null) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload === "number" || typeof payload === "boolean") return String(payload);
  if (typeof payload === "object") {
    const p = payload as Record<string, unknown>;
    // Shape from ccSubmit(label, value)
    if ("label" in p && "value" in p) {
      const label = String(p.label ?? "");
      const val = p.value;
      const valStr =
        val && typeof val === "object" ? kv(val as Record<string, unknown>) : String(val);
      return label ? `${label} — ${valStr}` : valStr;
    }
    return kv(p);
  }
  return String(payload);
}

export default function SandboxedHtml({
  html,
  height,
  onAction,
  iconNames,
  chromeless = false,
}: SandboxedHtmlProps): React.ReactElement {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const [autoHeight, setAutoHeight] = useState<number>(height ?? 120);

  // Read live rather than taking a prop. Every call site used to derive the
  // colour mode with the identical two lines and thread it down (in
  // GenerativeUINode, through the whole node tree) purely to reach this
  // component — five copies of one expression.
  const mode = useMode();

  // One pack, in the bundle, resolved synchronously — so this no longer waits
  // on an Iconify collection or re-runs when one arrives (the theming engine
  // was retired 2026-08-31).
  const icons = useMemo(() => buildIconMap(iconNames, 18, 40), [iconNames]);

  // `mode` and `icons` are deliberately absent: both change when somebody
  // switches colour mode, and rebuilding srcDoc remounts the document — a
  // published app would lose whatever the person had typed into it. First
  // paint reads them here; every change after that arrives as a patch in the
  // effect below.
  const srcDoc = useMemo(
    () => buildSrcDoc(html, THEME, mode, icons),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [html],
  );

  useEffect(() => {
    const frame = frameRef.current;
    const win = frame?.contentWindow;
    if (!win) return;
    // "*" because the frame has an OPAQUE origin — there is no origin string
    // that would match it, so a targeted postMessage could never be delivered.
    // Safe here: the payload is CSS variable values, and the frame is the only
    // recipient (we hold its window handle directly).
    win.postMessage(
      { __cc: true, kind: "theme", mode, vars: appTokenMap(THEME, mode), icons },
      "*",
    );
  }, [mode, icons, srcDoc]);

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      // Only trust messages from OUR frame's contentWindow, and only our shape.
      const frame = frameRef.current;
      if (!frame || ev.source !== frame.contentWindow) return;
      const data = ev.data;
      if (!data || typeof data !== "object" || (data as { __cc?: unknown }).__cc !== true) {
        return;
      }
      const kind = (data as { kind?: string }).kind;
      if (kind === "action") {
        const action = String((data as { action?: unknown }).action ?? "").slice(0, 2000);
        if (action) onAction?.(action);
      } else if (kind === "submit") {
        // A value the user set (slider/text/select) → turn it into a follow-up
        // message so the agent receives the chosen data on the same onAction path.
        const payload = (data as { payload?: unknown }).payload;
        const msg = describeSubmit(payload).slice(0, 2000);
        if (msg) onAction?.(msg);
      } else if (kind === "height" && height == null) {
        const h = Number((data as { height?: unknown }).height);
        // Clamp so a runaway document can't grow unbounded.
        if (Number.isFinite(h) && h > 0) setAutoHeight(Math.min(Math.max(h, 40), 2000));
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onAction, height]);

  // Chrome-less: fill the container (side-panel report pane), no header/frame.
  if (chromeless) {
    return (
      <iframe
        ref={frameRef}
        sandbox="allow-scripts"
        allow=""
        referrerPolicy="no-referrer"
        title="Generated document"
        srcDoc={srcDoc}
        style={{ width: "100%", height: height ?? "100%", border: "0", display: "block" }}
      />
    );
  }

  return (
    <div className="rounded-lg border border-border/60 bg-card/40 overflow-hidden">
      <div className="flex items-center gap-1.5 px-2.5 py-1 border-b border-border/50 bg-secondary/40">
        <span className="text-[9px] uppercase tracking-wide text-muted-foreground">
          Generated UI
        </span>
        <span className="ml-auto text-[9px] text-muted-foreground/60">sandboxed</span>
      </div>
      <iframe
        ref={frameRef}
        // allow-scripts ONLY — no allow-same-origin (opaque origin, no ambient authority).
        sandbox="allow-scripts"
        // Belt-and-suspenders: also block by feature policy.
        allow=""
        referrerPolicy="no-referrer"
        title="Generated UI"
        srcDoc={srcDoc}
        style={{ width: "100%", height: height ?? autoHeight, border: "0", display: "block" }}
      />
    </div>
  );
}
