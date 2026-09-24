"use client";

/**
 * GenerativeUINode — a SAFE, declarative generative-UI renderer.
 *
 * Agents push a `generative_ui` CUSTOM AG-UI event carrying a component TREE
 * (data, never code) which renders as real UI inside chat. This is the
 * "generative UI over our AG-UI custom channel + renderer registry" approach
 * (see specs/chat_ui_agui_hitl_review_2026-07.md): we adopt the PATTERN that
 * Prefab/FastMCP-Apps embody, but over our own AG-UI transport — no MCP-Apps
 * host, no arbitrary HTML/JS.
 *
 * Four tiers of richness, all safe:
 *   • Tier 1 — the whitelisted primitives below (card/table/badge/…): inert data,
 *     no code path. The default and safest.
 *   • Tier 2 — `template` node: renders a pre-designed, animated React component
 *     from TEMPLATE_REGISTRY by NAME, supplying only data. Our design, every time.
 *   • Tier 3 — `html` node: agent-GENERATED HTML/CSS/JS, executed inside a
 *     locked-down opaque-origin iframe (SandboxedHtml). Unlimited/animated but
 *     fully isolated — no ambient authority, actions bridged via postMessage.
 *   • Tier 4 — `react` node: an agent-authored React COMPONENT, bundled
 *     server-side (SandboxedReact) and run in that same isolated frame. Real
 *     state/hooks/effects for immersive artifacts, same zero authority.
 * There is NO in-tree raw-markup injection: an unknown `type` renders as an inert
 * labelled fallback, and neither the `html` nor `react` tier's code ever touches
 * our DOM/origin.
 *
 * Interactivity: `button` nodes carry an `action` string; clicking one calls
 * onAction(action), which the chat wires to submit the action as a follow-up
 * (same contract as the ```choices``` MCQ block). Template buttons and sandboxed
 * [data-cc-action] elements use the SAME onAction contract. No client-side eval
 * of agent code in our context.
 */

import { createElement } from "react";

import { MarkdownBody } from "@/components/MarkdownMessage";
import SandboxedHtml from "@/components/SandboxedHtml";
import Button from "@/components/ui/Button";
import SandboxedReact from "@/components/SandboxedReact";
import { renderTemplate } from "@/components/genUITemplates";
import { tableCells, tableColumns, text } from "@/lib/genUiText";
import { resolveIcon } from "@/lib/icons";
import { type AccentHue, accentForHue } from "@/lib/statusAccent";

// ─── Schema ────────────────────────────────────────────────────────────────

/** One node in the generative-UI tree. `type` MUST be a whitelisted kind. */
export interface GenUINode {
  type: string;
  props?: Record<string, unknown>;
  children?: GenUINode[];
}

/** The whitelisted component kinds. Extend deliberately — each is inert data. */
const KNOWN_TYPES = new Set([
  "card", "stack", "row", "heading", "text", "markdown", "badge",
  "divider", "keyValue", "table", "list", "code", "link", "button", "callout",
  "template", "html", "react", "icon",
]);

/**
 * An agent's tone → the status hue that draws it. One vocabulary with the
 * rest of the product (`lib/statusAccent.ts`), so a badge, a callout and an
 * icon read in light mode, in dark mode and under any accent. Until the S8
 * visual review these were raw palette classes and hex values tuned for dark
 * mode only.
 */
export const TONE_HUE: Record<string, AccentHue> = {
  success: "green",
  error: "red",
  danger: "red",
  warning: "amber",
  info: "blue",
};

/** tone → icon colour, as a CSS variable. */
const ICON_TONE: Record<string, string> = {
  success: "var(--success)",
  error: "var(--destructive)",
  warning: "var(--warning)",
  info: "var(--info)",
  muted: "var(--muted-foreground)",
  neutral: "var(--foreground)",
};

/**
 * Coerce an agent-supplied prop to display text — the ONE funnel for every
 * node type, so no shape mismatch can ever render "[object Object]" (it
 * shipped that way in a table header row when columns arrived as {key,label}).
 * Pure + unit-tested in lib/genUiText.test.ts.
 */
const s = text;

// ─── Node renderer ───────────────────────────────────────────────────────

function Node({
  node, onAction, depth = 0,
}: {
  node: GenUINode;
  onAction?: (action: string) => void;
  depth?: number;
}): React.ReactElement | null {
  // Depth guard — a pathological/looping tree can't blow the stack.
  if (depth > 20) return null;
  if (!node || typeof node !== "object") return null;
  const type = s(node.type);
  const props = (node.props ?? {}) as Record<string, unknown>;
  const kids = Array.isArray(node.children) ? node.children : [];

  const renderKids = () =>
    kids.map((k, i) => <Node key={i} node={k} onAction={onAction} depth={depth + 1} />);

  switch (type) {
    case "card":
      return (
        <div className="rounded-lg border border-border/60 bg-card/50 p-3 space-y-2">
          {props.title != null && (
            <div className="text-sm font-semibold text-foreground">{s(props.title)}</div>
          )}
          {renderKids()}
        </div>
      );

    case "stack":
      return <div className="space-y-2">{renderKids()}</div>;

    case "row":
      return <div className="flex flex-wrap items-center gap-2">{renderKids()}</div>;

    case "heading":
      return <div className="text-sm font-semibold text-foreground">{s(props.text)}</div>;

    case "text":
      return (
        <p className={`text-[13px] leading-relaxed ${
          props.muted ? "text-muted-foreground" : "text-foreground"
        }`}>{s(props.text)}</p>
      );

    case "markdown":
      return (
        <div className="min-w-0 text-[13px] leading-relaxed text-foreground">
          <MarkdownBody content={s(props.text)} />
        </div>
      );

    case "badge": {
      const hue = TONE_HUE[s(props.tone, "neutral")];
      const toneCls = hue
        ? `border-transparent ${accentForHue(hue).chip}`
        : "border-border text-muted-foreground bg-secondary/50";
      return (
        <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded border ${toneCls}`}>
          {s(props.text)}
        </span>
      );
    }

    case "divider":
      return <hr className="border-border/60 my-1" />;

    case "keyValue": {
      const pairs = Array.isArray(props.pairs) ? props.pairs : [];
      return (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[12px]">
          {pairs.map((p, i) => {
            const pair = (p ?? {}) as Record<string, unknown>;
            return (
              <div key={i} className="contents">
                <dt className="text-muted-foreground">{s(pair.key)}</dt>
                <dd className="text-foreground">{s(pair.value)}</dd>
              </div>
            );
          })}
        </dl>
      );
    }

    case "table": {
      // Columns arrive as plain strings OR as objects ({key,label}, {title},
      // {header}) — models emit both, and rows may be positional arrays or
      // objects keyed by the column key. tableColumns/tableCells normalise all
      // of it (and are unit-tested against the "[object Object]" header row
      // this once shipped).
      const rows = Array.isArray(props.rows) ? props.rows : [];
      const cols = tableColumns(props.columns, rows);
      const hasHeader = cols.some((c) => c.label.trim());
      return (
        <div className="overflow-x-auto rounded-md border border-border/60">
          <table className="w-full text-left text-[12px]">
            {hasHeader && (
              <thead className="bg-secondary/60">
                <tr>{cols.map((c, i) => (
                  <th key={i} className="px-2 py-1 font-medium text-muted-foreground">{c.label}</th>
                ))}</tr>
              </thead>
            )}
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-t border-border/60">
                  {tableCells(r, cols).map((cell, ci) => (
                    <td key={ci} className="px-2 py-1 text-foreground">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    case "list": {
      const items = Array.isArray(props.items) ? props.items : [];
      const ordered = !!props.ordered;
      const Tag = ordered ? "ol" : "ul";
      return (
        <Tag className={`ml-5 space-y-0.5 text-[13px] text-foreground ${
          ordered ? "list-decimal" : "list-disc"
        } list-outside marker:text-muted-foreground`}>
          {items.map((it, i) => <li key={i}>{s(it)}</li>)}
        </Tag>
      );
    }

    case "code":
      return (
        <pre className="rounded-md bg-muted border border-border p-2.5 overflow-x-auto text-[11px] text-foreground font-mono">
          {s(props.text)}
        </pre>
      );

    case "link": {
      const href = s(props.href);
      // Only http(s) links — never javascript:/data: (injection guard).
      const safe = /^https?:\/\//i.test(href);
      if (!safe) return <span className="text-muted-foreground">{s(props.text, href)}</span>;
      return (
        <a href={href} target="_blank" rel="noopener noreferrer"
          className="text-primary underline underline-offset-2 hover:opacity-80 break-all text-[13px]">
          {s(props.text, href)}
        </a>
      );
    }

    case "button": {
      const action = s(props.action);
      const label = s(props.label, "Action");
      const tone = s(props.tone, "default");
      // The one control primitive (DESIGN_SYSTEM.md rule 3), so the button
      // wears the theme's focus ring and state layer in both modes.
      const variant =
        tone === "primary" ? "primary" : tone === "danger" ? "destructive" : "secondary";
      return (
        <Button
          type="button"
          variant={variant}
          size="sm"
          disabled={!action || !onAction}
          onClick={() => action && onAction?.(action)}
        >
          {label}
        </Button>
      );
    }

    case "callout": {
      const accent = accentForHue(TONE_HUE[s(props.tone, "info")] ?? "blue");
      return (
        <div className={`rounded-md border border-border border-l-2 px-3 py-2 space-y-1 ${accent.bar} ${accent.soft}`}>
          {props.title != null && (
            <div className="text-[12px] font-semibold text-foreground">{s(props.title)}</div>
          )}
          {props.text != null && (
            <div className="text-[12px] text-muted-foreground">{s(props.text)}</div>
          )}
          {renderKids()}
        </div>
      );
    }

    case "icon": {
      // A Lucide icon by name (kebab/camel/Pascal accepted). On-brand, bundled,
      // no network. Unknown names fall back to a neutral glyph, never crash.
      const size = typeof props.size === "number" ? props.size : 16;
      const color = ICON_TONE[s(props.tone, "neutral")] ?? ICON_TONE.neutral;
      const label = s(props.label);
      // createElement (not <Icon/>) so the resolved Lucide component isn't seen
      // as a component declared during render (react-hooks/static-components).
      const glyph = createElement(resolveIcon(s(props.name)), {
        size, color, strokeWidth: 1.75,
        "aria-hidden": label ? undefined : true,
        "aria-label": label || undefined,
      });
      if (!label) return <span className="inline-flex align-middle">{glyph}</span>;
      return (
        <span className="inline-flex items-center gap-1.5 align-middle text-[13px] text-foreground">
          {glyph}
          <span>{label}</span>
        </span>
      );
    }

    case "template": {
      // Tier 2 — render a pre-designed animated component by name (data-only).
      // Interactive templates (formCard, optionPicker) receive the same
      // onAction channel buttons use, so their submits flow back to the agent.
      const name = s(props.name);
      const data = props.data;
      return renderTemplate(name, data, { onAction });
    }

    case "html": {
      // Tier 3 — agent-generated markup/JS, executed ONLY inside the isolated
      // opaque-origin iframe. Actions bridge back through the same onAction path.
      const code = s(props.code ?? props.html);
      if (!code) return null;
      const height =
        typeof props.height === "number" ? props.height : undefined;
      // The frame resolves the icons the agent declared into inline SVG, from
      // the active theme's pack, with no network (ccIcon / [data-cc-icon]).
      return (
        <SandboxedHtml
          html={code}
          height={height}
          onAction={onAction}
          iconNames={props.icons}
        />
      );
    }

    case "react": {
      // Tier 4 — a real React component. Compiled server-side into a
      // self-contained bundle, then run in the SAME isolated frame as the html
      // tier, with the same action bridge. Richer than `html` (state, hooks,
      // effects) with no extra authority.
      const code = s(props.code);
      if (!code) return null;
      const height = typeof props.height === "number" ? props.height : undefined;
      return (
        <SandboxedReact
          code={code}
          height={height}
          onAction={onAction}
          iconNames={props.icons}
        />
      );
    }

    default:
      // Unknown type → inert, labelled fallback. NEVER render raw props/markup.
      if (!KNOWN_TYPES.has(type)) {
        return (
          <div className="rounded border border-dashed border-border/60 px-2 py-1 text-[11px] text-muted-foreground">
            unsupported UI element{type ? `: ${type}` : ""}
          </div>
        );
      }
      return null;
  }
}

// ─── Public component ────────────────────────────────────────────────────

/**
 * Render a generative-UI tree (the value of a `generative_ui` CUSTOM event).
 * Accepts either a single node or `{ root: node }` / `{ view: node }` wrappers.
 */
export default function GenerativeUINode({
  spec, onAction,
}: {
  spec: unknown;
  onAction?: (action: string) => void;
}): React.ReactElement | null {
  const root =
    spec && typeof spec === "object"
      ? ((spec as Record<string, unknown>).root
        ?? (spec as Record<string, unknown>).view
        ?? spec)
      : spec;
  if (!root || typeof root !== "object") return null;
  return <Node node={root as GenUINode} onAction={onAction} />;
}
