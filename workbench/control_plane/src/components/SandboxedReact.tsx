"use client";

/**
 * SandboxedReact — runs an agent-authored React component inside the artifact
 * sandbox.
 *
 * Tier 4 of generative UI (see GenerativeUINode): where the `html` tier gives
 * agents raw DOM, this one gives them real components — state, hooks, effects,
 * derived values — for genuinely immersive artifacts. Same surfaces as every
 * other tier: an inline card in chat, or `chromeless` for a full-page document
 * in the side panel.
 *
 * ── How it stays safe ──────────────────────────────────────────────────────
 * This component owns none of the security model. It compiles the source (see
 * lib/compileArtifact — untrusted input, strict import allowlist) and hands the
 * resulting script to SandboxedHtml, which owns the opaque-origin iframe, the
 * CSP, the cc-* design system, and the postMessage bridge. Delegating rather
 * than duplicating means a React artifact and an HTML artifact cannot drift
 * apart on styling or on isolation.
 *
 * ── How it compiles (task 2432) ────────────────────────────────────────────
 * Through `lib/artifactCompileClient`: one compile at a time from this tab,
 * and a retry of a passing refusal ("busy") after its Retry-After. A card that
 * the server could not build yet says so, with "Try again", and never says
 * that the build failed. The member's rate limit shows its own sentence. A
 * changed source waits for a pause in the typing (`scheduleCompile`).
 *
 * ── Why the mount is deferred to `load` ────────────────────────────────────
 * SandboxedHtml appends its bridge script *after* the body content, because the
 * bridge fills [data-cc-icon] placeholders and so must run once they exist. Our
 * bundle is part of that body content, so it parses first — mounting
 * synchronously would run React before ccAction/ccSubmit are defined and break
 * any component that submits during mount. Waiting for `load` puts the mount
 * after every inline script in the document.
 */

import { useEffect, useRef, useState } from "react";

import SandboxedHtml from "@/components/SandboxedHtml";
import Button from "@/components/ui/Button";
import { cachedBundle, requestCompile } from "@/lib/artifactCompileClient";
import { scheduleCompile } from "@/lib/compileDebounce";

interface SandboxedReactProps {
  /** Agent-authored React source. Must default-export a component. */
  code: string;
  /** Fixed height (px). Omit to auto-size via the bridge. */
  height?: number;
  /** Interactions bubble up as follow-up messages, as with every other tier. */
  onAction?: (action: string) => void;
  /** Lucide icon NAMES the code references; the frame resolves them with the
   *  active theme's pack. */
  iconNames?: unknown;
  /** Fill-height, no chrome — for full-page artifacts in the side panel. */
  chromeless?: boolean;
}

type State =
  | { status: "compiling" }
  | { status: "ready"; js: string }
  /** The source's fault: the build failed. */
  | { status: "error"; error: string }
  /** The server was busy through every retry: not built YET. */
  | { status: "unavailable"; error: string }
  /** The member's rate limit. */
  | { status: "limited"; error: string };

/** `</script>` inside the bundle would close our tag early — neutralise it. */
function escapeForScript(js: string): string {
  return js.replace(/<\/(script)/gi, "<\\/$1");
}

function mountMarkup(js: string): string {
  return (
    '<div id="root"></div>\n' +
    `<script>window.addEventListener("load",function(){${escapeForScript(js)}});</script>`
  );
}

export default function SandboxedReact({
  code,
  height,
  onAction,
  iconNames,
  chromeless = false,
}: SandboxedReactProps): React.ReactElement {
  const initial = (source: string): State => {
    const cached = cachedBundle(source);
    return cached ? { status: "ready", js: cached } : { status: "compiling" };
  };

  const [state, setState] = useState<State>(() => initial(code));
  // Reset on a source change by adjusting state DURING RENDER — React's
  // recommended alternative to a reset effect, and it lets an already-cached
  // bundle render on the first pass with no "Building…" flash.
  const [renderedFor, setRenderedFor] = useState(code);
  if (renderedFor !== code) {
    setRenderedFor(code);
    setState(initial(code));
  }

  // "Try again" bumps this, which runs the effect once more for the same code.
  const [attempt, setAttempt] = useState(0);

  // Whether this frame has sent a compile yet. The first source compiles at
  // once. A later one waits for a pause in the typing (task 2432): the route
  // counts each compile against the member's rate limit.
  const compiledOnce = useRef(false);

  useEffect(() => {
    // Cached sources were resolved during render; nothing async to do.
    if (cachedBundle(code) !== undefined) return;
    // `cancelled` also guards against a slow compile for older source landing
    // after a newer one: React runs this cleanup before re-running the effect.
    // The same cleanup drops a compile that still waits for its pause.
    let cancelled = false;
    const cancelPause = scheduleCompile(!compiledOnce.current, () => {
      compiledOnce.current = true;
      void requestCompile(code).then((outcome) => {
        if (cancelled) return;
        if (outcome.kind === "ok") setState({ status: "ready", js: outcome.js });
        else if (outcome.kind === "error") setState({ status: "error", error: outcome.error });
        else if (outcome.kind === "limited") setState({ status: "limited", error: outcome.error });
        else setState({ status: "unavailable", error: outcome.error });
      });
    });
    return () => {
      cancelled = true;
      cancelPause();
    };
  }, [code, attempt]);

  const tryAgain = () => {
    setState({ status: "compiling" });
    setAttempt((n) => n + 1);
  };

  if (state.status === "compiling") {
    return (
      <div
        className={
          chromeless
            ? "flex h-full items-center justify-center"
            : "flex items-center justify-center rounded-lg border border-border/60 bg-card/40 py-8"
        }
      >
        <span className="text-xs text-muted-foreground animate-pulse">Building…</span>
      </div>
    );
  }

  if (state.status === "unavailable" || state.status === "limited") {
    // Not the source's fault, so not the destructive look of "failed to build".
    return (
      <div className="overflow-hidden rounded-lg border border-border/60 bg-card/40">
        <div className="border-b border-border/50 bg-secondary/40 px-2.5 py-1 text-[9px] uppercase tracking-wide text-muted-foreground">
          {state.status === "limited" ? "Artifact build limit reached" : "Artifact not built yet"}
        </div>
        <div className="flex items-center justify-between gap-3 p-3">
          <p className="text-xs text-muted-foreground">{state.error}</p>
          <Button variant="secondary" size="sm" onClick={tryAgain}>
            Try again
          </Button>
        </div>
      </div>
    );
  }

  if (state.status === "error") {
    // Surfaced verbatim: these diagnostics are what the agent needs to fix the
    // artifact, and what the user needs to know it is broken rather than empty.
    return (
      <div className="overflow-hidden rounded-lg border border-destructive/40 bg-destructive/5">
        <div className="border-b border-destructive/30 bg-destructive/10 px-2.5 py-1 text-[9px] uppercase tracking-wide text-destructive">
          React artifact failed to build
        </div>
        <pre className="overflow-x-auto whitespace-pre-wrap p-3 font-mono text-[11px] leading-relaxed text-destructive">
          {state.error}
        </pre>
      </div>
    );
  }

  return (
    <SandboxedHtml
      html={mountMarkup(state.js)}
      height={height}
      onAction={onAction}
      iconNames={iconNames}
      chromeless={chromeless}
    />
  );
}
