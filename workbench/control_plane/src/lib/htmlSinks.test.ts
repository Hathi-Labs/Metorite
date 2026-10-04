/**
 * Every raw-HTML sink in the control plane, with its verdict.
 *
 * A raw-HTML sink hands a string to the browser's HTML parser and skips
 * React's escaping: `dangerouslySetInnerHTML`, an `innerHTML` or `outerHTML`
 * write, `insertAdjacentHTML`, `document.write`, and an iframe `srcdoc`. Each
 * one that can carry content from an agent, an email, a member or a file must
 * pass a gate first. This test keeps the list, so a new sink fails here until
 * somebody writes down why it is safe.
 *
 * It is a source scan. It proves the sink is on the list and that the named
 * gate is wired in. `e2e/untrusted-html.spec.ts` proves the docx gate works
 * in a browser, and each gate's own tests prove the rest.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));

const SINK = /dangerouslySetInnerHTML|\.(?:innerHTML|outerHTML|srcdoc)\s*=(?!=)|insertAdjacentHTML|document\.write|srcDoc=\{/g;

/** Comments removed, so a sink named in prose does not count. */
const code = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`\\])\/\/[^\n]*/g, "$1");

/** File → the number of sinks in it, and why each is safe. */
const INVENTORY: Record<string, { count: number; verdict: string; mustContain?: string[] }> = {
  "app/layout.tsx": {
    count: 1,
    verdict: "The theme boot script. Static, built from app code, no input.",
    mustContain: ["themeBootScript()"],
  },
  "app/email/components/EmailList.tsx": {
    count: 1,
    verdict: "The search highlight. renderHighlight escapes & < > first, then puts back only <mark>.",
    mustContain: ['.replace(/</g, "&lt;")'],
  },
  "app/email/components/MessageContent.tsx": {
    count: 1,
    verdict: "The mail body, in a sandboxed iframe srcdoc: DOMPurify, a CSP, and no allow-scripts.",
    mustContain: ["sanitizeEmailHtml(html, showImages)", "UNTRUSTED_FORBID_TAGS", "script-src 'none'"],
  },
  "app/email/lib/signature.ts": {
    count: 1,
    verdict: "Decodes entities in a <textarea>, which parses as text. Tags are stripped first.",
    mustContain: ['document.createElement("textarea")'],
  },
  "app/build/apps/lib/testRunner.ts": {
    count: 1,
    verdict: "A built app under test, in a sandboxed iframe without allow-same-origin.",
  },
  "components/ArtifactViewerModal.tsx": {
    count: 2,
    verdict:
      "Shiki's highlighted code (Shiki escapes the source), and the .docx view, which is sanitizeDocxHtml's output.",
    mustContain: ["sanitizeDocxHtml(result.value)"],
  },
  "components/SandboxedHtml.tsx": {
    count: 2,
    verdict: "Agent HTML in a sandboxed iframe with an opaque origin and a CSP that forbids every fetch.",
    mustContain: ["sandbox="],
  },
  "lib/theme/sandbox-frame.ts": {
    count: 2,
    verdict: "Icon SVG from the app's own icon registry, written inside the sandbox frame.",
  },
};

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const found: Record<string, number> = {};
const text: Record<string, string> = {};
for (const full of sources(SRC)) {
  const rel = relative(SRC, full).split(sep).join("/");
  const src = readFileSync(full, "utf8");
  const n = (code(src).match(SINK) ?? []).length;
  if (n > 0) {
    found[rel] = n;
    text[rel] = src;
  }
}

describe("every raw-HTML sink is on the list", () => {
  it("finds the sinks it guards", () => {
    // Guards the scan against going vacuous.
    expect(Object.keys(found).length).toBeGreaterThanOrEqual(6);
  });

  it("no sink exists outside the inventory, and no count moved", () => {
    const actual = Object.fromEntries(Object.entries(found).sort());
    const expected = Object.fromEntries(
      Object.entries(INVENTORY).map(([f, v]) => [f, v.count]).sort(),
    );
    expect(actual).toEqual(expected);
  });

  it("each sink's gate is still wired in", () => {
    const missing: string[] = [];
    for (const [file, entry] of Object.entries(INVENTORY)) {
      for (const needle of entry.mustContain ?? []) {
        if (!text[file]?.includes(needle)) missing.push(`${file}: ${needle}`);
      }
    }
    expect(missing).toEqual([]);
  });
});
