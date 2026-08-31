/**
 * React artifacts (Tier 4) — end-to-end through the real compiler and a real
 * browser, with no dev server involved.
 *
 * What this guards that a unit test cannot:
 *
 *  1. The compiled bundle actually RUNS under the sandbox CSP
 *     (`default-src 'none'`, no network) in an opaque-origin iframe. A bundle
 *     that merely compiles proves nothing about executing there.
 *  2. Hooks re-render — the Preact/compat aliasing in compileArtifact is
 *     invisible to the agent, so a regression there would show up only at
 *     runtime, as a component that renders once and then freezes.
 *  3. `ccSubmit` works FROM MOUNT. SandboxedHtml appends its bridge script
 *     after the body content, so a synchronous mount would run React before
 *     ccAction/ccSubmit exist. SandboxedReact defers the mount to `load` to fix
 *     that, and this is the only place that ordering is actually exercised.
 *  4. The cc-* design tokens reach React artifacts, so they cannot drift away
 *     from HTML artifacts visually.
 */
import { expect, test } from "@playwright/test";

import { compileArtifact } from "../src/lib/compileArtifact";
import { appTokenCss } from "../src/lib/theme/app-tokens";
import { THEME } from "../src/lib/theme/themes";

/** An agent-authored artifact using the API surface agents are told to use. */
const SOURCE = `
import { useState, useMemo, useEffect } from "react";

export default function Dashboard() {
  const [n, setN] = useState(2);
  const doubled = useMemo(() => n * 2, [n]);
  useEffect(() => { window.ccSubmit("mounted", n); }, []);
  return (
    <div className="cc-report">
      <p className="cc-eyebrow">Live</p>
      <h1 id="title">Dashboard</h1>
      <div className="cc-v" id="val">{doubled}</div>
      <input id="slider" type="range" min="1" max="9" value={n}
             onChange={(e) => setN(+e.target.value)} />
      <button id="send" onClick={() => window.ccSubmit("Count", doubled)}>Send</button>
    </div>
  );
}`;

/** Mirrors the sandbox frame: same CSP, content first, bridge LAST. The token
 *  block is imported rather than copied — a stale copy would keep passing while
 *  the real one drifted, which is the exact failure this line guards. */
const CSP = [
  "default-src 'none'", "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline' 'unsafe-eval'", "img-src data:", "font-src data:",
  "connect-src 'none'", "form-action 'none'", "base-uri 'none'",
].join("; ");

const BRIDGE =
  `<script>window.ccSubmit=function(l,v){parent.postMessage(` +
  `{__cc:true,kind:"submit",payload:{label:String(l),value:v}},"*")};<` + `/script>`;

function buildHost(js: string): string {
  // Same shape as SandboxedReact.mountMarkup — the load deferral is the point.
  const escaped = js.replace(/<\/(script)/gi, "<\\/$1");
  const mount =
    `<div id="root"></div><script>window.addEventListener("load",function(){${escaped}});<` +
    `/script>`;
  const srcDoc =
    `<!doctype html><html data-theme="dark"><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${CSP}">` +
    `<style>:root{\n${appTokenCss(THEME, "dark")}\n}` +
    `.cc-eyebrow{color:var(--cc-primary)}</style>` +
    `</head><body>${mount}${BRIDGE}</body></html>`;
  return (
    `<!doctype html><html><body><iframe id="f" sandbox="allow-scripts" ` +
    `style="width:700px;height:500px;border:0" srcdoc="${srcDoc.replace(/"/g, "&quot;")}"></iframe>` +
    `<script>window.__msgs=[];addEventListener("message",e=>{` +
    `if(e.data&&e.data.__cc)window.__msgs.push(e.data)});<` + `/script></body></html>`
  );
}

test("a React artifact renders, stays interactive, and reaches the agent", async ({ page }) => {
  const compiled = await compileArtifact(SOURCE);
  expect(compiled.ok, compiled.ok ? "" : (compiled as { error: string }).error).toBe(true);
  if (!compiled.ok) return;

  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });

  await page.setContent(buildHost(compiled.js));
  const frame = page.frameLocator("#f");

  // 1. Rendered under the CSP at all.
  await expect(frame.locator("#title")).toHaveText("Dashboard");

  // 2. Hooks: useMemo recomputes on state change (2*2 -> 2*5).
  await expect(frame.locator("#val")).toHaveText("4");
  await frame.locator("#slider").fill("5");
  await expect(frame.locator("#val")).toHaveText("10");

  // 4. cc-* tokens reach the artifact — using the REAL token block rather than
  //    a hand-written `--cc-primary`, so this also proves the values the sandbox
  //    actually injects survive the CSP. RapidTool dark primary is
  //    hsl(198 89% 50%) == rgb(14,173,241).
  await expect(frame.locator(".cc-eyebrow")).toHaveCSS("color", "rgb(14, 173, 241)");

  await frame.locator("#send").click();
  const messages = await page.evaluate(
    () => (window as unknown as { __msgs: { payload: { label: string; value: unknown } }[] }).__msgs,
  );

  // 3. The mount-time submit landed, proving the bridge existed at mount, and
  //    the click submit carries the current derived value.
  expect(messages.map((m) => m.payload.label)).toEqual(["mounted", "Count"]);
  expect(messages[1].payload.value).toBe(10);

  expect(errors).toEqual([]);
});

test("a broken artifact fails with diagnostics instead of rendering blank", async () => {
  const res = await compileArtifact(
    'import db from "pg";\nexport default function A(){ return <p>{String(db)}</p>; }',
  );
  expect(res.ok).toBe(false);
  if (res.ok) return;
  expect(res.error).toMatch(/Cannot import/);
});
