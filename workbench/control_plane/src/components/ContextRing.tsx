"use client";

import React from "react";

// ── Context-window ring ─────────────────────────────────────────────────────
/**
 * Small circular SVG progress ring showing context-window usage.
 * Always shows the token count. Clickable to trigger manual /compact when ≥ 60%.
 * Color transitions: primary (< 60%) → warning (60–79%) → destructive (≥ 80%).
 *
 * Hover (desktop) or tap (mobile) reveals a detail popup with used/total tokens
 * and percentage.  Click away or mouse-out dismisses it.
 *
 * ⚠️ It is NOT run progress (owner report, 2026-10-19: a "19%" ring under a
 * run that looked idle). It says how full the model's context window is, and
 * its name says so to a screen reader and on hover. It never pulses with a
 * run: the run status row (`RunStatusRow.tsx`) is the only run indicator.
 */
export default function ContextRing({
  pct,
  usedTokens,
  totalTokens,
  compacting,
  onCompact,
  modelId,
}: {
  pct: number;
  usedTokens: number;
  totalTokens: number;
  compacting: boolean;
  onCompact?: () => void;
  modelId?: string;
}) {
  const r = 9;
  const circ = 2 * Math.PI * r;
  const filled = Math.min(1, pct / 100) * circ;
  // NOTE: the theme defines these vars as FULL colors (e.g.
  // `--primary: hsl(198 89% 50%)`), so use `var(--x)` directly — wrapping them
  // in `hsl(var(--x))` yields `hsl(hsl(...))`, which is invalid and renders
  // nothing (this is why the ring was invisible and only the % text showed).
  const color =
    pct >= 80
      ? "var(--destructive)"
      : pct >= 60
        ? "var(--warning)"
        : "var(--primary)";
  const canCompact = pct >= 60 && onCompact && !compacting;
  // The ring's name: what it measures, so nobody reads it as run progress.
  const ringName = `Context window ${pct}% full: ${usedTokens.toLocaleString()} of ${totalTokens.toLocaleString()} tokens used`;

  // ── Popup state ──────────────────────────────────────────────────────
  const [showPopup, setShowPopup] = React.useState(false);
  const containerRef = React.useRef<HTMLSpanElement>(null);

  // Click-away: close popup when clicking outside the container.
  React.useEffect(() => {
    if (!showPopup) return;
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowPopup(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showPopup]);

  // ── Mini progress bar color (use computed `color` directly) ──────────
  const barColor = color; // "var(--primary)" etc. — works in style={}

  // ── Detail popup ─────────────────────────────────────────────────────
  const popup = showPopup && (
    <div
      className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50
        rounded-lg border border-border bg-popover px-3 py-2.5 shadow-xl
        text-xs whitespace-nowrap
        animate-in fade-in slide-in-from-bottom-1 duration-150"
    >
      {/* Arrow */}
      <div className="absolute top-full left-1/2 -translate-x-1/2
        w-0 h-0 border-l-4 border-r-4 border-t-4
        border-l-transparent border-r-transparent border-t-border" />

      <div className="flex items-center gap-2 mb-1.5">
        <span className={`font-semibold ${
          pct >= 80 ? "text-destructive" : pct >= 60 ? "text-warning" : "text-foreground"
        }`}>
          Context window {pct}% full
        </span>
        {modelId && (
          <span className="text-[10px] text-muted-foreground truncate max-w-[120px]">
            {modelId}
          </span>
        )}
      </div>

      {/* Mini progress bar */}
      <div className="w-full h-1.5 rounded-full bg-border/50 mb-2 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${Math.min(100, pct)}%`, backgroundColor: barColor }}
        />
      </div>

      <div className="flex items-center justify-between gap-4 text-[10px]">
        <span className="text-muted-foreground">Used</span>
        <span className="font-mono font-medium text-foreground tabular-nums">
          {usedTokens.toLocaleString()}
        </span>
      </div>
      <div className="flex items-center justify-between gap-4 text-[10px]">
        <span className="text-muted-foreground">Total</span>
        <span className="font-mono font-medium text-foreground tabular-nums">
          {totalTokens.toLocaleString()}
        </span>
      </div>
      <div className="flex items-center justify-between gap-4 text-[10px]">
        <span className="text-muted-foreground">Remaining</span>
        <span className="font-mono font-medium text-foreground tabular-nums">
          {Math.max(0, totalTokens - usedTokens).toLocaleString()}
        </span>
      </div>
    </div>
  );

  // ── Shared hover / tap handlers ──────────────────────────────────────
  const hoverProps = {
    onMouseEnter: () => setShowPopup(true),
    onMouseLeave: () => setShowPopup(false),
    onClick: (e: React.MouseEvent) => {
      // Toggle popup on tap (mobile).  On desktop hover handles show/hide.
      // Don't toggle if the click landed on the /compact button itself —
      // let that button fire its own onCompact handler.
      const target = e.target as HTMLElement;
      if (target.closest("button")) return;
      setShowPopup((v) => !v);
    },
  };

  // ── Ring content (filling donut gauge + % label) ─────────────────────
  // The arc fills clockwise from 12 o'clock as context is consumed.  The
  // PRIMARY label is the percentage (so the user reads "how full") — the raw
  // token fraction lives in the hover popup.
  const ring = (
    <span className="shrink-0 flex items-center gap-1.5" role="img" aria-label={ringName} data-context-ring>
      <svg width="18" height="18" viewBox="0 0 24 24" className="shrink-0" aria-hidden="true">
        {/* Background track */}
        <circle cx="12" cy="12" r={r} fill="none" strokeWidth="3"
          style={{ stroke: "var(--border)" } as React.CSSProperties} />
        {/* Progress arc — starts at 12 o'clock, fills clockwise */}
        <circle
          cx="12" cy="12" r={r}
          fill="none"
          strokeWidth="3"
          strokeDasharray={`${filled} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 12 12)"
          style={{ stroke: color, transition: "stroke-dasharray 0.4s ease" } as React.CSSProperties}
        />
      </svg>
      <span className={`text-[11px] font-mono font-semibold tabular-nums ${
        pct >= 80 ? "text-destructive" : pct >= 60 ? "text-warning" : "text-muted-foreground"
      }`}>
        {pct}%
      </span>
    </span>
  );

  // ── Compacting spinner overlaid on the ring ────────────────────────
  if (compacting) {
    return (
      <span className="shrink-0 flex items-center gap-1 text-[10px] text-primary/70 animate-pulse px-1.5 py-0.5 rounded-md">
        {/* Show the filled ring + a spinning overlay so user sees both fill level and activity */}
        <span className="relative w-4 h-4 shrink-0">
          <svg width="16" height="16" viewBox="0 0 24 24" className="absolute inset-0">
            <circle cx="12" cy="12" r={r} fill="none" strokeWidth="2"
              style={{ stroke: "var(--border)" } as React.CSSProperties} />
            <circle cx="12" cy="12" r={r} fill="none" strokeWidth="2"
              strokeDasharray={`${filled} ${circ}`} strokeLinecap="round"
              style={{ stroke: color, transform: "rotate(-90deg)", transformOrigin: "center" } as React.CSSProperties} />
          </svg>
          <svg width="16" height="16" viewBox="0 0 24 24" className="absolute inset-0 animate-spin" fill="none">
            <path d="M12 3a9 9 0 0 1 9 9" strokeWidth="2" strokeLinecap="round"
              style={{ stroke: "var(--primary)" } as React.CSSProperties} />
          </svg>
        </span>
        <span className="hidden sm:inline">Compacting…</span>
      </span>
    );
  }

  // ── /compact button (≥ 60%) ──────────────────────────────────────────
  if (canCompact) {
    return (
      <span ref={containerRef} className="relative" {...hoverProps}>
        {popup}
        <button
          type="button"
          onClick={onCompact}
          title={`${ringName}. Compact the conversation`}
          className="shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded-md hover:bg-warning/10 tech-transition">
          {ring}
          <span className="hidden sm:inline text-[9px] text-warning font-medium">/compact</span>
        </button>
      </span>
    );
  }

  // ── Normal ring — still, whatever the run does ──────────────────────
  return (
    <span ref={containerRef} className="relative" {...hoverProps}>
      {popup}
      <span title={ringName} className="shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded-md cursor-default">
        {ring}
      </span>
    </span>
  );
}
