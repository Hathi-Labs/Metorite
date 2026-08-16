/**
 * The sandbox frame — the document a generated app actually runs in.
 *
 * Custom Apps, generative-UI cards and React artifacts all execute inside an
 * `<iframe sandbox="allow-scripts">` with an opaque origin, so they inherit
 * NOTHING from us: not our stylesheet, not `data-theme`, not one custom
 * property. This module builds the entire world they wake up in — the CSP, the
 * `--cc-*` token block, the `.cc-*` component kit, and the `postMessage` bridge.
 *
 * It is deliberately free of React and of every `@/` import except the theme
 * tokens, for two reasons. It is not a component — nothing here renders, it
 * emits a string. And it is the piece worth testing in a real browser
 * (`e2e/sandbox-theming.spec.ts` drives THIS, not a hand-written copy of it:
 * a mirror of the frame would pass happily while the frame was broken).
 *
 * Everything in here is a **published interface**. `--cc-*` names and `.cc-*`
 * classes are written into `agent-app-builder`'s instructions, into
 * `acb_skills/design.md`, and into every app already built and shipped — none
 * of which we can go back and edit. Rename one and you break apps you cannot
 * see. Adding is safe; removing is not.
 */

// Relative, not `@/…`: this module is imported directly by the Playwright
// spec that drives the real frame, and Playwright transpiles without the
// app's path alias.
import { appTokenCss } from "./app-tokens";
import type { Theme, ThemeMode } from "./types";

// A nonce-free but locked CSP: allow inline style/script (the generated code
// IS inline), forbid every remote fetch surface. 'unsafe-inline'/'unsafe-eval'
// are scoped to this opaque-origin frame only — they grant the generated code
// nothing outside its own sandbox.
const FRAME_CSP = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline' 'unsafe-eval'",
  "img-src data:",
  "font-src data:",
  "connect-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
].join("; ");

/** The bridge injected into every frame: exposes ccAction/ccSubmit/ccIcon +
 *  auto-height, and applies live theme patches. `__ICONS__` is replaced with the
 *  pre-resolved { name: svgString } JSON.
 *
 *  Exported for `e2e/sandbox-theming.spec.ts`, which drives the REAL frame in a
 *  real browser rather than a hand-written copy of it — a mirror of this file
 *  would pass while the thing it mirrors was broken. */
export const BRIDGE = `
<script>
  (function () {
    var ICONS = __ICONS__;
    // The ONLY way generated code talks to the app. Everything else is walled off.
    window.ccAction = function (action) {
      try { parent.postMessage({ __cc: true, kind: "action", action: String(action) }, "*"); }
      catch (e) {}
    };
    // Submit a VALUE the user set (slider/text/select) back to the agent. Sends a
    // structured follow-up so the agent gets both a human label and the raw data.
    // ccSubmit("Temperature", 22)  or  ccSubmit({ temp: 22, unit: "C" })
    window.ccSubmit = function (label, value) {
      var payload = (arguments.length <= 1) ? label
        : { label: String(label), value: value };
      try { parent.postMessage({ __cc: true, kind: "submit", payload: payload }, "*"); }
      catch (e) {}
    };
    // Return the inline SVG string for a pre-resolved Lucide icon name (or "").
    window.ccIcon = function (name) { return ICONS[name] || ""; };
    // Delegate clicks on [data-cc-action] so agents don't need to wire handlers.
    document.addEventListener("click", function (e) {
      var el = e.target && e.target.closest ? e.target.closest("[data-cc-action]") : null;
      if (el) window.ccAction(el.getAttribute("data-cc-action"));
    });
    // Delegate [data-cc-submit] — collect the value of the named form control(s)
    // in the same form/container and submit them. Works for a bare click ("Apply"
    // button) that harvests every [name] input within its closest form or [data-cc-form].
    document.addEventListener("click", function (e) {
      var trigger = e.target && e.target.closest ? e.target.closest("[data-cc-submit]") : null;
      if (!trigger) return;
      var label = trigger.getAttribute("data-cc-submit") || "";
      var scope = trigger.closest("form, [data-cc-form]") || document;
      var fields = scope.querySelectorAll("input[name], select[name], textarea[name]");
      if (fields.length === 0) { window.ccSubmit(label); return; }
      var out = {};
      Array.prototype.forEach.call(fields, function (f) {
        if ((f.type === "checkbox" || f.type === "radio") && !f.checked) return;
        out[f.getAttribute("name")] = f.value;
      });
      window.ccSubmit(label, out);
    });
    // Auto-fill <span data-cc-icon="Name"></span> placeholders with the SVG.
    Array.prototype.forEach.call(document.querySelectorAll("[data-cc-icon]"), function (el) {
      var svg = ICONS[el.getAttribute("data-cc-icon")];
      if (svg) el.innerHTML = svg;
    });
    // Live theme changes. The srcDoc already carries the right tokens for the
    // FIRST paint (no flash); this is what keeps a RUNNING app in step when
    // somebody switches theme in Settings. It has to be a patch and not a
    // re-render: rebuilding srcDoc remounts the document, and a half-filled
    // form in a published app would be thrown away every time.
    //
    // setProperty is a value API, not a text parser — a value carrying a ';'
    // is rejected by the CSSOM rather than opening a new declaration — so a
    // hostile sibling frame that got a handle to this window can at worst
    // recolour it. The key guard keeps it to our own namespace.
    window.addEventListener("message", function (e) {
      var d = e.data;
      if (!d || typeof d !== "object" || d.__cc !== true || d.kind !== "theme") return;
      var vars = d.vars;
      if (!vars || typeof vars !== "object") return;
      var root = document.documentElement;
      Object.keys(vars).forEach(function (k) {
        if (k.slice(0, 5) !== "--cc-") return;
        root.style.setProperty(k, String(vars[k]));
      });
      if (d.mode === "light" || d.mode === "dark") {
        root.style.setProperty("color-scheme", d.mode);
        root.setAttribute("data-theme", d.mode);
      }
      // Icons are a theme choice too, so a switch re-resolves them from the new
      // pack. Placeholders can be re-filled in place; markup an app already
      // inlined with ccIcon() cannot be, which is exactly why data-cc-icon is
      // the documented path for static markup.
      if (d.icons && typeof d.icons === "object") {
        ICONS = d.icons;
        Array.prototype.forEach.call(
          document.querySelectorAll("[data-cc-icon]"),
          function (el) {
            var svg = ICONS[el.getAttribute("data-cc-icon")];
            if (svg) el.innerHTML = svg;
          }
        );
      }
    });
    function reportHeight() {
      var h = Math.max(
        document.body ? document.body.scrollHeight : 0,
        document.documentElement ? document.documentElement.scrollHeight : 0
      );
      try { parent.postMessage({ __cc: true, kind: "height", height: h }, "*"); } catch (e) {}
    }
    window.addEventListener("load", reportHeight);
    if (window.ResizeObserver) new ResizeObserver(reportHeight).observe(document.documentElement);
    setTimeout(reportHeight, 50);
    setTimeout(reportHeight, 300);
  })();
</script>`;

/** JSON for injection into a <script>: escape `<`/`>` and the U+2028/U+2029 line
 *  separators (legal in JSON, illegal bare in a JS string literal) so the value
 *  cannot break out of the script context. Uses charCode literals to avoid
 *  embedding the raw separators in this source file. */
function safeScriptJson(value: unknown): string {
  const LS = String.fromCharCode(0x2028);
  const PS = String.fromCharCode(0x2029);
  return JSON.stringify(value ?? {})
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .split(LS).join("\\u2028")
    .split(PS).join("\\u2029");
}

/** Turn a ccSubmit payload into a natural follow-up message the agent can read.
 *  ccSubmit("Temperature", 22)      → 'Temperature: 22'
 *  ccSubmit({temp:22,unit:"C"})     → 'temp: 22, unit: C'
 *  ccSubmit("Apply", {size:"L"})    → 'Apply — size: L'
 *  ccSubmit("Confirm")              → 'Confirm' */


export function buildSrcDoc(
  html: string,
  theme: Theme,
  mode: ThemeMode,
  icons: Record<string, string>,
): string {
  // Base styling gives generated code the REAL Metorite design system —
  // the ACTIVE theme's tokens, not a frozen copy of one — so generated HTML is
  // on-brand by default without the agent needing to know a single colour
  // value. Generated <style> can override.
  //
  // Every `--cc-*` value comes from `lib/theme/app-tokens`, which is the whole
  // point: these used to be hand-written RapidTool literals switching only on
  // light/dark, so a published app stayed RapidTool-blue while the shell around
  // it turned Fluent or Material. The theming engine stopped at this boundary.
  // Now the boundary IS the engine's published interface — see that module for
  // why `--cc-*` is a separate, stable vocabulary rather than our own tokens.
  const base = `
<style>
  :root {
    color-scheme: ${mode};
${appTokenCss(theme, mode)}
  }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--cc-font, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif);
    color: var(--cc-fg);
    background: transparent;
    padding: 2px;
    font-size: 13px;
    line-height: 1.5;
  }
  * { box-sizing: border-box; }
  a { color: var(--cc-primary); }
  h1, h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
  [data-cc-icon] svg { display: inline-block; vertical-align: middle; }

  /* On-brand native controls so generated interactive UI matches the app.
     min-height keeps every control's TAP target comfortable on a touch
     screen (published apps run in whoever's browser opens them, phone
     included) without changing the compact visual size — padding still
     controls how the control LOOKS, min-height only pads the hit area.
     44px matches Apple HIG / Material Design's minimum touch target size —
     the same number Metorite's own mobile bottom-nav bar already uses
     (see globals.css), not a separate convention for built apps. */
  button, .cc-btn {
    font: inherit; cursor: pointer; border-radius: calc(var(--cc-radius) - 0.25rem);
    border: 1px solid var(--cc-border); background: var(--cc-secondary);
    color: var(--cc-fg); padding: 0.4rem 0.8rem; min-height: 2.75rem;
    transition: background 0.2s var(--cc-ease), border-color 0.2s var(--cc-ease);
  }
  button:hover, .cc-btn:hover { border-color: var(--cc-primary); }
  button.cc-primary, .cc-btn.cc-primary {
    background: var(--cc-primary); color: var(--cc-primary-fg); border-color: transparent;
  }
  input, select, textarea {
    font: inherit; color: var(--cc-fg); background: var(--cc-card);
    border: 1px solid var(--cc-border); border-radius: calc(var(--cc-radius) - 0.25rem);
    padding: 0.35rem 0.55rem; outline: none; min-height: 2.75rem;
  }
  input:focus, select:focus, textarea:focus {
    border-color: var(--cc-primary);
    box-shadow: 0 0 0 calc(var(--cc-control-focus-ring, 2px) + 1px)
      color-mix(in srgb, var(--cc-primary) 20%, transparent);
  }
  /* Native checkbox/radio sizing is a well-established browser default —
     don't stretch it to the min-height above. */
  input[type="checkbox"], input[type="radio"] { min-height: 0; }
  input[type="range"] {
    -webkit-appearance: none; appearance: none; height: 6px; min-height: 0; padding: 0;
    border: none; border-radius: 999px; background: var(--cc-secondary);
  }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none; width: 16px; height: 16px;
    border-radius: 999px; background: var(--cc-primary); cursor: pointer;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--cc-primary) 18%, transparent);
  }
  input[type="range"]::-moz-range-thumb {
    width: 16px; height: 16px; border: none; border-radius: 999px;
    background: var(--cc-primary); cursor: pointer;
  }
  .cc-card {
    background: var(--cc-card); border: 1px solid var(--cc-border);
    border-radius: var(--cc-radius); padding: 0.9rem;
  }

  /* ── Report design kit ────────────────────────────────────────────────
     Reusable, on-brand document blocks so an agent writes terse semantic
     HTML and gets a polished Metorite report (matches the reference
     proposal artifact). Every class is namespaced cc-*; agents compose them.
     Mono/utility face for eyebrows, section numbers, diagrams, and data. */
  .cc-report {
    --cc-mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace;
    max-width: 62rem; margin: 0 auto; padding: 0.5rem 0.25rem 2rem;
    font-size: 14px; line-height: 1.62;
  }
  .cc-report > * + * { margin-top: 0.9rem; }
  .cc-report p { max-width: 72ch; color: var(--cc-muted); }
  .cc-report strong { color: var(--cc-fg); font-weight: 620; }
  .cc-report code {
    font-family: var(--cc-mono); font-size: 0.86em;
    background: var(--cc-secondary); border: 1px solid var(--cc-border);
    padding: 1px 5px; border-radius: 5px; color: var(--cc-fg);
  }
  /* Eyebrow — mono uppercase kicker with a leading rule. */
  .cc-eyebrow {
    font-family: var(--cc-mono); font-size: 11px; letter-spacing: 0.14em;
    text-transform: uppercase; color: var(--cc-primary);
    display: flex; align-items: center; gap: 10px; margin: 0;
  }
  .cc-eyebrow::before {
    content: ""; width: 22px; height: 1px; background: var(--cc-primary);
  }
  /* Section number — mono accent tag that precedes an h2. */
  .cc-sec-num {
    font-family: var(--cc-mono); font-size: 11px; letter-spacing: 0.1em;
    color: var(--cc-accent); text-transform: uppercase; display: block;
  }
  .cc-report h1 { font-size: clamp(24px, 4vw, 34px); line-height: 1.1; letter-spacing: -0.02em; text-wrap: balance; }
  .cc-report h2 { font-size: 21px; letter-spacing: -0.015em; margin: 0.2rem 0 0.1rem; text-wrap: balance; }
  .cc-report h3 { font-size: 15px; margin: 1.1rem 0 0.3rem; }
  .cc-lede { font-size: 16px; color: var(--cc-muted); max-width: 64ch; }
  /* Callout — tinted panel (accent by default, .cc-callout-key for primary).
     Subtle tint + border, NOT a thick side stripe. */
  .cc-callout {
    display: grid; grid-template-columns: auto 1fr; gap: 14px;
    background: color-mix(in srgb, var(--cc-accent) 8%, var(--cc-card));
    border: 1px solid color-mix(in srgb, var(--cc-accent) 24%, var(--cc-border));
    border-radius: var(--cc-radius); padding: 0.85rem 1rem;
  }
  .cc-callout .cc-tag {
    font-family: var(--cc-mono); font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.1em; color: var(--cc-accent); padding-top: 2px; white-space: nowrap;
    /* Grid children stretch by default; the tag must hug its text and stay at
       the top of a multi-line callout, not run the height of the panel. */
    align-self: start; height: fit-content;
  }
  .cc-callout p { margin: 0; max-width: none; }
  .cc-callout-key {
    background: color-mix(in srgb, var(--cc-primary) 8%, var(--cc-card));
    border-color: color-mix(in srgb, var(--cc-primary) 26%, var(--cc-border));
  }
  .cc-callout-key .cc-tag { color: var(--cc-primary); }
  /* Chips row. */
  .cc-chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .cc-chip {
    font-family: var(--cc-mono); font-size: 11px; padding: 4px 10px;
    border: 1px solid var(--cc-border); border-radius: 999px;
    color: var(--cc-muted); background: var(--cc-card);
  }
  .cc-chip b { color: var(--cc-fg); font-weight: 600; }
  /* Grid of cards. */
  .cc-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .cc-report .cc-grid > .cc-card { margin: 0; }
  .cc-grid .cc-card h4 { margin: 0 0 6px; font-size: 14px; font-weight: 620; display: flex; align-items: center; gap: 8px; }
  .cc-grid .cc-card p { font-size: 13px; }
  .cc-dot { width: 8px; height: 8px; border-radius: 999px; flex: none; background: var(--cc-primary); }
  /* Comparison table. */
  .cc-compare { overflow-x: auto; border: 1px solid var(--cc-border); border-radius: var(--cc-radius); }
  .cc-compare table { border-collapse: collapse; width: 100%; font-size: 13px; min-width: 30rem; }
  .cc-compare th, .cc-compare td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--cc-border); vertical-align: top; }
  .cc-compare thead th {
    font-family: var(--cc-mono); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--cc-muted); background: var(--cc-secondary);
  }
  .cc-compare tbody tr:last-child td { border-bottom: none; }
  .cc-compare td:first-child { color: var(--cc-muted); }
  .cc-yes { color: var(--cc-success); font-weight: 600; }
  .cc-no { color: var(--cc-danger); font-weight: 600; }
  .cc-partial { color: var(--cc-warning); font-weight: 600; }
  /* Mono diagram block — ASCII architecture / flow. */
  .cc-diagram {
    background: var(--cc-bg); border: 1px solid var(--cc-border);
    border-radius: var(--cc-radius); padding: 1rem 1.1rem; overflow-x: auto;
  }
  .cc-diagram pre {
    margin: 0; font-family: var(--cc-mono); font-size: 12px; line-height: 1.5;
    color: var(--cc-muted); white-space: pre;
  }
  .cc-diagram pre b { color: var(--cc-primary); font-weight: 600; }
  .cc-diagram pre .cc-hl { color: var(--cc-accent); }
  /* Numbered steps. */
  .cc-steps { display: grid; gap: 0; }
  .cc-step { display: grid; grid-template-columns: 34px 1fr; gap: 14px; padding: 0.8rem 0; border-bottom: 1px solid var(--cc-border); align-items: start; }
  .cc-step:last-child { border-bottom: none; }
  .cc-step .cc-n {
    font-family: var(--cc-mono); font-size: 12px; color: var(--cc-primary);
    border: 1px solid var(--cc-border); border-radius: 8px; width: 30px; height: 30px;
    display: grid; place-items: center; background: var(--cc-card);
  }
  .cc-step h4 { margin: 0.15rem 0 0.2rem; font-size: 14px; font-weight: 620; }
  .cc-step p { margin: 0; font-size: 13px; }
  /* Phase ribbon. */
  .cc-phase { display: grid; grid-template-columns: auto 1fr; gap: 16px; padding: 0.85rem 0; border-bottom: 1px solid var(--cc-border); }
  .cc-phase:last-child { border-bottom: none; }
  /* Badge — a solid label chip. Deliberately NOT scoped to .cc-phase: agents
     reuse it inside .cc-callout, table cells and headings, and when it was
     phase-only it either came out unstyled or, as a bare grid child, stretched
     to the full row height — a solid block of primary colour instead of a chip.
     The fit-content + self alignment keeps it chip-sized in ANY grid or flex
     parent, which is the whole failure mode. */
  .cc-badge {
    font-family: var(--cc-mono); font-size: 11px; font-weight: 600; color: var(--cc-primary-fg);
    background: var(--cc-primary); border-radius: 8px; padding: 4px 11px; white-space: nowrap;
    display: inline-flex; align-items: center;
    width: fit-content; height: fit-content;
    align-self: start; justify-self: start;
  }
  .cc-phase h4 { margin: 0.1rem 0 0.3rem; font-size: 15px; }
  .cc-phase ul { margin: 0.3rem 0 0; padding-left: 18px; font-size: 13px; color: var(--cc-muted); }
  /* Status pills for tables/inline. */
  .cc-pill { font-family: var(--cc-mono); font-size: 10px; padding: 2px 8px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }

  /* ── Data-viz & decision blocks ───────────────────────────────────────
     Pure CSS / inline-SVG so an agent emits terse markup (no JS, no hand-
     rolled SVG paths) and gets an on-brand chart, KPI, decision box, or
     architecture diagram. Tone via .cc-t-{success,warning,danger,accent}. */

  /* KPI stat tiles — big number is the hero. Row of .cc-stat in .cc-stats. */
  .cc-stats { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
  .cc-report .cc-stats { margin-top: 0.9rem; }
  .cc-stat {
    background: var(--cc-card); border: 1px solid var(--cc-border);
    border-radius: var(--cc-radius); padding: 0.9rem 1rem;
  }
  .cc-stat .cc-k {
    font-family: var(--cc-mono); font-size: 10px; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--cc-muted); margin: 0 0 6px;
  }
  .cc-stat .cc-v {
    font-size: 30px; font-weight: 640; line-height: 1; color: var(--cc-fg);
    font-variant-numeric: tabular-nums; letter-spacing: -0.02em;
  }
  /* Unit/suffix. Needs its own gap and line-height: without them a wordy unit
     ("risk-adjusted", "near-close") jams straight into the number, and inherits
     line-height:1 so a wrapped second line collides with the digits above. */
  .cc-stat .cc-v small {
    font-size: 14px; font-weight: 500; color: var(--cc-muted);
    display: inline-block; margin-left: 0.3em; line-height: 1.25;
    letter-spacing: 0; vertical-align: baseline;
  }
  .cc-stat .cc-d { font-size: 12px; margin-top: 6px; font-variant-numeric: tabular-nums; }
  .cc-stat .cc-d.cc-up { color: var(--cc-success); }
  .cc-stat .cc-d.cc-down { color: var(--cc-danger); }
  .cc-stat .cc-d.cc-up::before { content: "▲ "; }
  .cc-stat .cc-d.cc-down::before { content: "▼ "; }
  /* Accent one stat tile: <div class="cc-stat cc-feature">. */
  .cc-stat.cc-feature {
    border-color: color-mix(in srgb, var(--cc-accent) 40%, var(--cc-border));
    background: color-mix(in srgb, var(--cc-accent) 7%, var(--cc-card));
  }
  .cc-stat.cc-feature .cc-v { color: var(--cc-accent); }

  /* Horizontal bar chart. Each row: <div class="cc-bar" style="--v:72">…
     <b>label</b><span>value</span>. --v is the percent (0–100). */
  .cc-bars { display: grid; gap: 9px; }
  .cc-report .cc-bars { margin-top: 0.6rem; }
  .cc-bar { display: grid; grid-template-columns: minmax(70px, 22%) 1fr auto; align-items: center; gap: 12px; }
  .cc-bar > b { font-weight: 500; font-size: 12px; color: var(--cc-muted); text-align: right;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cc-bar > .cc-track { height: 18px; border-radius: 6px; background: var(--cc-secondary); overflow: hidden; }
  .cc-bar > .cc-track::after {
    content: ""; display: block; height: 100%; width: calc(var(--v, 0) * 1%);
    background: var(--cc-primary); border-radius: 6px;
    transition: width 0.7s var(--cc-ease);
  }
  .cc-bar > span { font-family: var(--cc-mono); font-size: 12px; color: var(--cc-fg);
    font-variant-numeric: tabular-nums; min-width: 3ch; text-align: right; }
  .cc-bar.cc-t-success > .cc-track::after { background: var(--cc-success); }
  .cc-bar.cc-t-warning > .cc-track::after { background: var(--cc-warning); }
  .cc-bar.cc-t-danger  > .cc-track::after { background: var(--cc-danger); }
  .cc-bar.cc-t-accent  > .cc-track::after { background: var(--cc-accent); }

  /* Donut / ring gauge — one number, single conic-gradient. Set the percent:
     <div class="cc-donut" style="--v:64"><span>64<small>%</small></span><b>label</b></div>. */
  .cc-donuts { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); justify-items: center; }
  .cc-donut { display: grid; justify-items: center; gap: 8px; text-align: center; }
  .cc-donut > .cc-ring {
    width: 104px; height: 104px; border-radius: 50%; display: grid; place-items: center;
    background:
      radial-gradient(var(--cc-card) 58%, transparent 59%),
      conic-gradient(var(--cc-primary) calc(var(--v, 0) * 1%), var(--cc-secondary) 0);
  }
  .cc-donut.cc-t-success > .cc-ring { background: radial-gradient(var(--cc-card) 58%, transparent 59%), conic-gradient(var(--cc-success) calc(var(--v, 0) * 1%), var(--cc-secondary) 0); }
  .cc-donut.cc-t-warning > .cc-ring { background: radial-gradient(var(--cc-card) 58%, transparent 59%), conic-gradient(var(--cc-warning) calc(var(--v, 0) * 1%), var(--cc-secondary) 0); }
  .cc-donut.cc-t-danger  > .cc-ring { background: radial-gradient(var(--cc-card) 58%, transparent 59%), conic-gradient(var(--cc-danger)  calc(var(--v, 0) * 1%), var(--cc-secondary) 0); }
  .cc-donut.cc-t-accent  > .cc-ring { background: radial-gradient(var(--cc-card) 58%, transparent 59%), conic-gradient(var(--cc-accent)  calc(var(--v, 0) * 1%), var(--cc-secondary) 0); }
  .cc-donut .cc-ring span { font-size: 22px; font-weight: 640; color: var(--cc-fg); font-variant-numeric: tabular-nums; }
  .cc-donut .cc-ring span small { font-size: 12px; color: var(--cc-muted); }
  .cc-donut > b { font-weight: 500; font-size: 12px; color: var(--cc-muted); }

  /* Sparkline holder — agent drops a tiny inline <svg><polyline>. Styles the
     stroke/fill so they need no attributes: <div class="cc-spark"><svg …>. */
  .cc-spark svg { display: block; width: 100%; height: auto; overflow: visible; }
  .cc-spark polyline, .cc-spark path { fill: none; stroke: var(--cc-primary); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .cc-spark .cc-fill { fill: color-mix(in srgb, var(--cc-primary) 14%, transparent); stroke: none; }
  .cc-spark .cc-dot { fill: var(--cc-accent); stroke: none; }

  /* Decision / recommendation box — verdict as the hero, rationale below.
     Tone via .cc-t-*; default reads as a recommendation (success). */
  .cc-decision {
    display: grid; grid-template-columns: auto 1fr; gap: 14px; align-items: start;
    border: 1px solid color-mix(in srgb, var(--cc-success) 34%, var(--cc-border));
    background: color-mix(in srgb, var(--cc-success) 7%, var(--cc-card));
    border-radius: var(--cc-radius); padding: 1rem 1.15rem;
  }
  .cc-decision > .cc-mark {
    width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center;
    font-weight: 700; font-size: 16px; color: var(--cc-primary-fg);
    background: var(--cc-success);
  }
  .cc-decision .cc-verdict {
    font-family: var(--cc-mono); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--cc-success); margin: 2px 0 4px;
  }
  .cc-decision h4 { margin: 0 0 4px; font-size: 16px; color: var(--cc-fg); }
  .cc-decision p { margin: 0; max-width: none; }
  .cc-decision.cc-t-warning { border-color: color-mix(in srgb, var(--cc-warning) 40%, var(--cc-border)); background: color-mix(in srgb, var(--cc-warning) 8%, var(--cc-card)); }
  .cc-decision.cc-t-warning > .cc-mark { background: var(--cc-warning); }
  .cc-decision.cc-t-warning .cc-verdict { color: var(--cc-warning); }
  .cc-decision.cc-t-danger { border-color: color-mix(in srgb, var(--cc-danger) 40%, var(--cc-border)); background: color-mix(in srgb, var(--cc-danger) 8%, var(--cc-card)); }
  .cc-decision.cc-t-danger > .cc-mark { background: var(--cc-danger); }
  .cc-decision.cc-t-danger .cc-verdict { color: var(--cc-danger); }

  /* Visual architecture diagram — flex rows of nodes joined by arrows, an
     upgrade from the ASCII .cc-diagram. Structure: .cc-arch > (.cc-node |
     .cc-arrow | .cc-arch-row). Add .cc-primary / a tone class to a node. */
  .cc-arch { display: flex; flex-wrap: wrap; align-items: stretch; gap: 10px; padding: 0.4rem 0; }
  .cc-arch-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; width: 100%; justify-content: center; }
  .cc-node {
    flex: 0 1 auto; min-width: 96px; text-align: center;
    background: var(--cc-card); border: 1px solid var(--cc-border);
    border-radius: 10px; padding: 0.55rem 0.85rem;
  }
  .cc-node .cc-node-t { font-size: 13px; font-weight: 600; color: var(--cc-fg); }
  .cc-node .cc-node-s { font-family: var(--cc-mono); font-size: 10px; color: var(--cc-muted); margin-top: 2px; }
  .cc-node.cc-primary { border-color: color-mix(in srgb, var(--cc-primary) 55%, var(--cc-border)); background: color-mix(in srgb, var(--cc-primary) 9%, var(--cc-card)); }
  .cc-node.cc-primary .cc-node-t { color: var(--cc-primary); }
  .cc-node.cc-accent  { border-color: color-mix(in srgb, var(--cc-accent) 55%, var(--cc-border)); background: color-mix(in srgb, var(--cc-accent) 9%, var(--cc-card)); }
  .cc-node.cc-accent .cc-node-t { color: var(--cc-accent); }
  .cc-node.cc-muted { border-style: dashed; }
  /* Arrows: default → ; add .cc-down for a ↓ between stacked rows. */
  .cc-arrow { display: grid; place-items: center; color: var(--cc-muted); font-family: var(--cc-mono); font-size: 15px; padding: 0 2px; }
  .cc-arrow::before { content: "\\2192"; }
  .cc-arrow.cc-down { width: 100%; }
  .cc-arrow.cc-down::before { content: "\\2193"; }
  .cc-arrow.cc-bi::before { content: "\\21C4"; }

  /* Legend row for charts / diagrams. */
  .cc-legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11px; color: var(--cc-muted); }
  .cc-legend > span { display: inline-flex; align-items: center; gap: 6px; }
  .cc-legend i { width: 10px; height: 10px; border-radius: 3px; background: var(--cc-primary); display: inline-block; }
  .cc-legend i.cc-t-success { background: var(--cc-success); }
  .cc-legend i.cc-t-warning { background: var(--cc-warning); }
  .cc-legend i.cc-t-danger { background: var(--cc-danger); }
  .cc-legend i.cc-t-accent { background: var(--cc-accent); }

  /* ── Status callouts (4 tones) ────────────────────────────────────────
     Small tinted notice, distinct from the verdict-style .cc-decision. Base
     is neutral; add .cc-info / .cc-success / .cc-warning / .cc-danger. */
  .cc-note {
    display: grid; grid-template-columns: auto 1fr; gap: 11px; align-items: start;
    border: 1px solid var(--cc-border); background: var(--cc-card);
    border-radius: calc(var(--cc-radius) - 0.15rem); padding: 0.7rem 0.9rem;
  }
  .cc-note > .cc-ico {
    width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center;
    font-size: 12px; font-weight: 700; color: var(--cc-primary-fg); background: var(--cc-muted);
  }
  .cc-note p { margin: 0; max-width: none; color: var(--cc-fg); font-size: 13px; }
  .cc-note p strong { color: var(--cc-fg); }
  .cc-note.cc-info { border-color: color-mix(in srgb, var(--cc-primary) 32%, var(--cc-border)); background: color-mix(in srgb, var(--cc-primary) 6%, var(--cc-card)); }
  .cc-note.cc-info > .cc-ico { background: var(--cc-primary); }
  .cc-note.cc-success { border-color: color-mix(in srgb, var(--cc-success) 32%, var(--cc-border)); background: color-mix(in srgb, var(--cc-success) 6%, var(--cc-card)); }
  .cc-note.cc-success > .cc-ico { background: var(--cc-success); }
  .cc-note.cc-warning { border-color: color-mix(in srgb, var(--cc-warning) 34%, var(--cc-border)); background: color-mix(in srgb, var(--cc-warning) 8%, var(--cc-card)); }
  .cc-note.cc-warning > .cc-ico { background: var(--cc-warning); color: var(--cc-warning-fg); }
  .cc-note.cc-danger { border-color: color-mix(in srgb, var(--cc-danger) 34%, var(--cc-border)); background: color-mix(in srgb, var(--cc-danger) 7%, var(--cc-card)); }
  .cc-note.cc-danger > .cc-ico { background: var(--cc-danger); }

  /* ── Data table + status cells ────────────────────────────────────────
     Richer than .cc-compare: zebra rows, a leading status-stripe column, and
     inline mini-bar / pill cells for ops & audit reports.
       <div class="cc-table"><table>… <td class="cc-num">42</td>
     A row can lead with a severity stripe: <td class="cc-stripe cc-t-danger">.
     Cell helpers: .cc-num (mono, right-aligned), .cc-dim (muted),
     .cc-status (dot + label), .cc-minibar (style="--v:74"), .cc-tag-pill. */
  .cc-table { overflow-x: auto; border: 1px solid var(--cc-border); border-radius: var(--cc-radius); }
  .cc-table table { border-collapse: collapse; width: 100%; font-size: 13px; min-width: 32rem; }
  .cc-table thead th {
    font-family: var(--cc-mono); font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--cc-muted); background: var(--cc-secondary); text-align: left;
    padding: 9px 13px; border-bottom: 1px solid var(--cc-border); white-space: nowrap;
  }
  .cc-table tbody td { padding: 9px 13px; border-bottom: 1px solid var(--cc-border); vertical-align: middle; color: var(--cc-fg); }
  .cc-table tbody tr:last-child td { border-bottom: none; }
  .cc-table tbody tr:nth-child(even) td { background: color-mix(in srgb, var(--cc-secondary) 45%, transparent); }
  .cc-table td.cc-num { font-family: var(--cc-mono); font-variant-numeric: tabular-nums; text-align: right; }
  .cc-table td.cc-dim { color: var(--cc-muted); }
  /* Leading status-stripe cell: <td class="cc-stripe cc-t-danger"></td>. */
  .cc-table td.cc-stripe { width: 4px; padding: 0; background: var(--cc-border); }
  .cc-table td.cc-stripe.cc-t-success { background: var(--cc-success); }
  .cc-table td.cc-stripe.cc-t-warning { background: var(--cc-warning); }
  .cc-table td.cc-stripe.cc-t-danger { background: var(--cc-danger); }
  .cc-table td.cc-stripe.cc-t-accent { background: var(--cc-accent); }
  /* Inline status text with a leading dot: <span class="cc-status cc-t-success">ok</span>. */
  .cc-status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; }
  .cc-status::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--cc-muted); }
  .cc-status.cc-t-success::before { background: var(--cc-success); }
  .cc-status.cc-t-warning::before { background: var(--cc-warning); }
  .cc-status.cc-t-danger::before { background: var(--cc-danger); }
  .cc-status.cc-t-accent::before { background: var(--cc-accent); }
  /* Inline mini-bar cell: <td><span class="cc-minibar" style="--v:74"></span></td>. */
  .cc-minibar { display: inline-block; width: 100%; min-width: 56px; height: 8px; border-radius: 999px; background: var(--cc-secondary); overflow: hidden; vertical-align: middle; }
  .cc-minibar::after { content: ""; display: block; height: 100%; width: calc(var(--v, 0) * 1%); background: var(--cc-primary); border-radius: 999px; }
  .cc-minibar.cc-t-success::after { background: var(--cc-success); }
  .cc-minibar.cc-t-warning::after { background: var(--cc-warning); }
  .cc-minibar.cc-t-danger::after { background: var(--cc-danger); }
  /* Solid status pill for a cell: <span class="cc-tag-pill cc-t-success">Live</span>. */
  .cc-tag-pill {
    font-family: var(--cc-mono); font-size: 10px; letter-spacing: 0.03em; padding: 2px 9px;
    border-radius: 999px; background: var(--cc-secondary); color: var(--cc-muted); white-space: nowrap;
  }
  .cc-tag-pill.cc-t-success { background: color-mix(in srgb, var(--cc-success) 18%, transparent); color: var(--cc-success); }
  .cc-tag-pill.cc-t-warning { background: color-mix(in srgb, var(--cc-warning) 20%, transparent); color: var(--cc-warning); }
  .cc-tag-pill.cc-t-danger { background: color-mix(in srgb, var(--cc-danger) 18%, transparent); color: var(--cc-danger); }
  .cc-tag-pill.cc-t-accent { background: color-mix(in srgb, var(--cc-accent) 18%, transparent); color: var(--cc-accent); }

  /* ── Timeline / roadmap (Gantt-style) ─────────────────────────────────
     Rows on a shared time axis. Set the number of columns on .cc-timeline
     (--cols, default 12) and place each bar with --s (start col, 1-based)
     and --e (span in cols):
       <div class="cc-timeline" style="--cols:12">
         <div class="cc-tl-row"><b>Design</b>
           <div class="cc-tl-track"><span class="cc-tl-bar" style="--s:1;--e:3">Design</span></div></div>
     Add a tone class to a bar to recolor it. */
  .cc-timeline { --cols: 12; display: grid; gap: 8px; }
  .cc-report .cc-timeline { margin-top: 0.6rem; }
  .cc-tl-row { display: grid; grid-template-columns: minmax(72px, 18%) 1fr; align-items: center; gap: 12px; }
  .cc-tl-row > b { font-weight: 500; font-size: 12px; color: var(--cc-muted); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cc-tl-track {
    display: grid; grid-template-columns: repeat(var(--cols), 1fr); gap: 0;
    height: 26px; border-radius: 7px; background: var(--cc-secondary);
    background-image: repeating-linear-gradient(90deg, transparent 0, transparent calc(100% / var(--cols) - 1px), var(--cc-border) calc(100% / var(--cols) - 1px), var(--cc-border) calc(100% / var(--cols)));
    overflow: hidden;
  }
  .cc-tl-bar {
    grid-column: var(--s, 1) / span var(--e, 1); align-self: stretch;
    display: flex; align-items: center; padding: 0 9px; margin: 3px 0;
    border-radius: 6px; font-size: 11px; font-weight: 600; color: var(--cc-primary-fg);
    background: var(--cc-primary); overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
  }
  .cc-tl-bar.cc-t-accent { background: var(--cc-accent); }
  .cc-tl-bar.cc-t-success { background: var(--cc-success); }
  .cc-tl-bar.cc-t-warning { background: var(--cc-warning); color: var(--cc-warning-fg); }
  .cc-tl-bar.cc-t-danger { background: var(--cc-danger); }
  .cc-tl-bar.cc-ghost { background: var(--cc-secondary); color: var(--cc-muted); border: 1px dashed var(--cc-border); }
  /* Axis labels row: same grid, .cc-tl-axis of <span>s under the tracks. */
  .cc-tl-axis { display: grid; grid-template-columns: minmax(72px, 18%) 1fr; gap: 12px; margin-top: 2px; }
  .cc-tl-axis > .cc-tl-ticks { display: grid; grid-template-columns: repeat(var(--cols), 1fr); font-family: var(--cc-mono); font-size: 9px; color: var(--cc-muted); }
  .cc-tl-axis .cc-tl-ticks > span { text-align: center; }

  /* ── Trend line / area chart ──────────────────────────────────────────
     A charted trend, bigger than .cc-spark, with a faint grid, area fill,
     axis labels, and an emphasized endpoint. Agent supplies the geometry as
     an inline <svg>; these styles theme it so no per-element attributes are
     needed. Structure:
       <div class="cc-chart">
         <svg class="cc-plot" viewBox="0 0 300 120" preserveAspectRatio="none">
           <g class="cc-grid"><line x1=… /></g>
           <polyline class="cc-area" points="…" />   (closed to baseline)
           <polyline class="cc-line" points="…" />
           <circle class="cc-end" cx=… cy=… r="3" /></svg>
         <div class="cc-x"><span>Jan</span>…</div></div>  */
  .cc-chart {
    background: var(--cc-card); border: 1px solid var(--cc-border);
    border-radius: var(--cc-radius); padding: 0.9rem 1rem 0.7rem;
  }
  .cc-chart .cc-plot { display: block; width: 100%; height: auto; overflow: visible; }
  .cc-chart .cc-grid line { stroke: var(--cc-border); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .cc-chart .cc-area { fill: color-mix(in srgb, var(--cc-primary) 14%, transparent); stroke: none; }
  .cc-chart .cc-line { fill: none; stroke: var(--cc-primary); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
  .cc-chart .cc-line.cc-t-accent { stroke: var(--cc-accent); }
  .cc-chart .cc-line.cc-t-success { stroke: var(--cc-success); }
  .cc-chart .cc-area.cc-t-accent { fill: color-mix(in srgb, var(--cc-accent) 14%, transparent); }
  .cc-chart .cc-end { fill: var(--cc-accent); stroke: var(--cc-card); stroke-width: 1.5; }
  .cc-chart .cc-x { display: flex; justify-content: space-between; font-family: var(--cc-mono); font-size: 10px; color: var(--cc-muted); margin-top: 6px; }
  .cc-chart .cc-x > span { flex: 1; text-align: center; }
  .cc-chart .cc-x > span:first-child { text-align: left; }
  .cc-chart .cc-x > span:last-child { text-align: right; }

  @media (prefers-reduced-motion: reduce) {
    .cc-bar > .cc-track::after { transition: none; }
  }
</style>`;
  const bridge = BRIDGE.replace("__ICONS__", safeScriptJson(icons));
  // `data-theme` here is the colour MODE, not the theme id — generated CSS has
  // used `[data-theme="dark"]` selectors since long before the theming engine,
  // and every already-published app carries them. Keeping the attribute meaning
  // what those apps assume is the compatible choice; the theme's identity
  // reaches them through the token values instead, which is where it belongs
  // (an app should not have to know a theme's NAME to look right in it).
  return `<!doctype html><html data-theme="${mode}"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${FRAME_CSP}">
${base}</head><body>${html}${bridge}</body></html>`;
}
