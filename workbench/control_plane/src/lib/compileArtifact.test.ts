import { execFileSync } from "node:child_process";

import { describe, expect, it } from "vitest";

import { BUSY, BuildSlot, COMPILE_LIMITS, compileArtifact } from "./compileArtifact";

/**
 * The esbuild service processes that THIS test process started. vitest runs
 * this file in a forked child (`pool: "forks"`), so the service is our child.
 */
function esbuildChildren(): number {
  if (process.platform === "win32") {
    const out = execFileSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `(Get-CimInstance Win32_Process -Filter "ParentProcessId=${process.pid}" | ` +
          `Where-Object { $_.Name -like 'esbuild*' } | Measure-Object).Count`,
      ],
      { encoding: "utf8" },
    );
    return Number(out.trim());
  }
  const out = execFileSync("ps", ["-o", "comm=", "--ppid", String(process.pid)], {
    encoding: "utf8",
  });
  return out.split("\n").filter((line) => line.includes("esbuild")).length;
}

/** Wait until the esbuild children count `n`, for at most `ms`. */
async function untilChildren(n: number, ms: number): Promise<number> {
  const end = Date.now() + ms;
  let seen = esbuildChildren();
  while (seen !== n && Date.now() < end) {
    await new Promise((r) => setTimeout(r, 100));
    seen = esbuildChildren();
  }
  return seen;
}

/** Nested arrows: esbuild's time grows faster than this input, minifier or not. */
const SLOW = `export default function A(){ const f = ${"()=>".repeat(12_000)}1; return <p>{String(f)}</p>; }`;

const COMPONENT = `
import { useState } from "react";

export default function Artifact() {
  const [n, setN] = useState(3);
  return <div className="cc-report"><h1>{n}</h1>
    <button onClick={() => setN(n + 1)}>inc</button></div>;
}
`;

// Each case starts a real esbuild build, and the first one also starts the
// esbuild service. Under a full CI run the first build took more than the
// default 5 s (home server, 2026-11-09), so this block allows 30 s.
describe("compileArtifact", { timeout: 30_000 }, () => {
  it("compiles a React component with hooks into a self-contained bundle", async () => {
    const res = await compileArtifact(COMPONENT);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    // Self-contained: the runtime is inlined, so nothing is left to import.
    expect(res.js).not.toMatch(/\bimport\s|\brequire\(/);
    expect(res.js).toContain("#root".slice(1)); // mounts into the root element
    expect(res.bytes).toBeGreaterThan(5_000);
  });

  it("keeps the bundle small by aliasing react to preact/compat", async () => {
    const res = await compileArtifact(COMPONENT);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    // Real react+react-dom lands near 194 KB; preact/compat near 19 KB. Guard the
    // 10x win so an accidental de-aliasing shows up as a failing test, not a
    // silently heavier chat.
    expect(res.bytes).toBeLessThan(60_000);
  });

  it("accepts TypeScript syntax", async () => {
    const res = await compileArtifact(
      "export default function A(): unknown { const x: number = 1; return <p>{x}</p>; }",
    );
    expect(res.ok).toBe(true);
  });

  // ── The allowlist is a security boundary, not a convenience ──────────────
  // This compiles untrusted model output on the server. Unrestricted resolution
  // would let an artifact inline arbitrary file contents into output we hand
  // back to the client.

  // NOTE: each case must *use* the imported binding. The tsx loader applies
  // TypeScript import elision, so an unused import is stripped before it is ever
  // resolved — harmless (nothing is read) but it would silently no-op the test.
  it.each([
    ["node builtin", "fs", '"fs"'],
    ["absolute path", "x", '"/etc/passwd"'],
    ["relative escape", "x", '"../../../../etc/hosts"'],
    ["sibling source", "x", '"./compileArtifact"'],
    ["third-party package", "x", '"lodash"'],
  ])("refuses to resolve %s", async (_label, binding, spec) => {
    const res = await compileArtifact(
      `import ${binding} from ${spec};\n` +
        `export default function A(){ return <p>{String(${binding})}</p>; }`,
    );
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error).toMatch(/Cannot import/);
  });

  it("elides an unused disallowed import rather than reading it", async () => {
    // Documents the elision above: nothing is resolved, so nothing is read.
    const res = await compileArtifact(
      'import unused from "/etc/passwd";\nexport default function A(){ return <p/>; }',
    );
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.js).not.toMatch(/root:|\/bin\/(ba)?sh/);
  });

  it("does not leak file contents when an import is refused", async () => {
    const res = await compileArtifact(
      'import x from "/etc/hostname";\n' +
        "export default function A(){ return <p>{String(x)}</p>; }",
    );
    expect(res.ok).toBe(false);
    if (res.ok) return;
    // The refusal names the specifier but never the file's bytes.
    expect(res.error).toContain("/etc/hostname");
  });

  it("allows the React runtime imports an artifact legitimately needs", async () => {
    const res = await compileArtifact(
      'import { useEffect, useMemo } from "react";\n' +
        "export default function A(){ useEffect(()=>{},[]); " +
        "const v = useMemo(()=>2,[]); return <p>{v}</p>; }",
    );
    expect(res.ok).toBe(true);
  });

  // ── The @cc/ui design kit ────────────────────────────────────────────────

  it("resolves the @cc/ui design kit", async () => {
    const res = await compileArtifact(
      'import { Report, Stat, Stats } from "@cc/ui";\n' +
        "export default function A(){ return <Report><Stats>" +
        '<Stat label="Revenue" value={18} unit="%" delta={12} />' +
        "</Stats></Report>; }",
    );
    expect(res.ok, res.ok ? "" : res.error).toBe(true);
    if (!res.ok) return;
    // The kit compiles to the same cc-* classes HTML artifacts use.
    expect(res.js).toContain("cc-stat");
    expect(res.js).toContain("cc-report");
  });

  it("tree-shakes unused components so one import stays cheap", async () => {
    const one = await compileArtifact(
      'import { Report } from "@cc/ui";\nexport default function A(){ return <Report/>; }',
    );
    const many = await compileArtifact(
      'import { Report, Table, Timeline, Chart, Donuts, Steps, Decision } from "@cc/ui";\n' +
        "export default function A(){ return <Report>" +
        "<Table columns={[]} rows={[]}/><Timeline rows={[]}/><Chart values={[1,2]}/>" +
        '<Donuts data={[]}/><Steps items={[]}/><Decision title="x"/>' +
        "</Report>; }",
    );
    expect(one.ok && many.ok).toBe(true);
    if (!one.ok || !many.ok) return;
    // Importing one component must not drag the whole library in — otherwise the
    // kit costs every artifact the same, which defeats the point.
    expect(one.bytes).toBeLessThan(many.bytes);
    expect(many.js).toContain("cc-timeline");
    expect(one.js).not.toContain("cc-timeline");
  });

  it("computes chart geometry so agents never hand-roll SVG points", async () => {
    const res = await compileArtifact(
      'import { Chart } from "@cc/ui";\n' +
        "export default function A(){ return <Chart values={[3,1,4,1,5]} labels={['a','e']}/>; }",
    );
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.js).toContain("cc-plot");
  });

  // ── Input hygiene ────────────────────────────────────────────────────────

  it("rejects empty source", async () => {
    expect((await compileArtifact("")).ok).toBe(false);
    expect((await compileArtifact("   ")).ok).toBe(false);
  });

  it("rejects oversized source instead of bundling it", async () => {
    const res = await compileArtifact("//" + "x".repeat(300 * 1024));
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error).toMatch(/larger than/);
  });

  // ── Diagnostics must stay affordable ─────────────────────────────────────
  // Error text is paid for twice: once when the agent reads it, once when it
  // re-emits the whole artifact to fix it. One mistake cascading into dozens of
  // repeated messages used to cost ~3.4k tokens.

  it("caps and de-duplicates diagnostics instead of returning one per bad line", async () => {
    const source =
      Array.from({ length: 60 }, (_, i) => `import x${i} from "pkg-${i}";`).join("\n") +
      "\nexport default function A(){ return <p>{" +
      Array.from({ length: 60 }, (_, i) => `String(x${i})`).join("+") +
      "}</p>; }";
    const res = await compileArtifact(source);
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error.length).toBeLessThan(2_000);
    expect(res.error.split("\n").length).toBeLessThanOrEqual(9);
    expect(res.error).toMatch(/and \d+ more distinct errors/);
    // Still actionable: the first offender is named.
    expect(res.error).toContain("pkg-0");
  });

  it("shows a repeat count rather than repeating an identical message", async () => {
    const res = await compileArtifact(
      'import a from "lodash";\nimport b from "lodash";\n' +
        "export default function A(){ return <p>{String(a)+String(b)}</p>; }",
    );
    expect(res.ok).toBe(false);
    if (res.ok) return;
    // Two imports of the same missing module collapse into one line.
    expect(res.error.split("\n").length).toBe(1);
  });

  it("reports syntax errors with a line number the agent can act on", async () => {
    const res = await compileArtifact("export default function A(){ return <div>unclosed; }");
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error).toMatch(/line \d+/);
  });

  // ── Import attributes (task 2432, F3) ─────────────────────────────────────
  // `with { type: "text" }` made esbuild inline the SOURCE of preact/compat.
  // Each case uses its binding, so TypeScript import elision cannot hide it.

  it.each([
    ["text on react", 'import t from "react" with { type: "text" };'],
    ["text on the kit", 'import t from "@cc/ui" with { type: "text" };'],
    ["json on react", 'import t from "react" with { type: "json" };'],
  ])("refuses an import attribute: %s", async (_label, line) => {
    const res = await compileArtifact(
      `${line}\nexport default function A(){ return <p>{String(t).length}</p>; }`,
    );
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toBe("source");
    expect(res.error).toMatch(/import attribute/);
    expect(res.error).toMatch(/Remove the `with \{ … \}` part/);
  });

  it("ignores the old `assert` form: it never changes the loader, so no source is inlined", async () => {
    // esbuild does not pass `assert { … }` to onResolve. Measured: it keeps the
    // js loader, so the import is a plain one and the bundle holds no module
    // source as text.
    const res = await compileArtifact(
      'import t from "react" assert { type: "text" };\n' +
        "export default function A(){ return <p>{String(t).length}</p>; }",
    );
    expect(res.ok, res.ok ? "" : res.error).toBe(true);
    if (!res.ok) return;
    expect(res.js).not.toMatch(/\bimport\s*\{/);
  });

  // ── The limits (task 2432, F1) ────────────────────────────────────────────

  it("pins the limits the route relies on", () => {
    expect(COMPILE_LIMITS).toEqual({
      maxSourceBytes: 256 * 1024,
      maxOutputBytes: 1024 * 1024,
      timeoutMs: 10_000,
      maxQueued: 4,
      maxWaitMs: 10_000,
    });
    expect(Object.isFrozen(COMPILE_LIMITS)).toBe(true);
  });

  it("builds 64 KB of nested ternaries fast, because the minifier is off", async () => {
    // With `minify: true` this source took 13 s on 2026-11-09, so it ran into
    // the 10 s timeout. Mutation: minify back on -> slow or timeout -> red.
    const source = `export default function A(){ return <p>{${"a?b:".repeat(16_384)}c}</p>; }`;
    expect(Buffer.byteLength(source)).toBeGreaterThan(64 * 1024);
    const started = Date.now();
    const res = await compileArtifact(source);
    const took = Date.now() - started;
    expect(res.ok, res.ok ? "" : res.error).toBe(true);
    expect(took).toBeLessThan(5_000);
  }, 30_000);

  it("refuses a bundle over the output cap, with the size and the next step", async () => {
    const res = await compileArtifact(COMPONENT, { maxOutputBytes: 1024 });
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toBe("output_too_large");
    expect(res.error).toMatch(/more than the 1 KB limit/);
    expect(res.error).toMatch(/Make the component smaller/);
  });

  it("STOPS esbuild when a build runs out of time, and the next build still works", async () => {
    // Start the service with a normal build, so there is a process to kill.
    expect((await compileArtifact(COMPONENT)).ok).toBe(true);
    expect(await untilChildren(1, 5_000)).toBe(1);

    const started = Date.now();
    const res = await compileArtifact(SLOW, { timeoutMs: 50 });
    const took = Date.now() - started;
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toBe("timeout");
    expect(res.error).toMatch(/Metorite stopped it/);
    expect(res.error).toMatch(/send it again/);
    expect(took).toBeLessThan(2_000);

    // The proof that the work ended: the esbuild process is gone. Without
    // `stop()` it would still grind the nested arrows for seconds.
    expect(await untilChildren(0, 3_000)).toBe(0);

    // A fresh service takes the next build.
    const next = await compileArtifact(COMPONENT);
    expect(next.ok, next.ok ? "" : next.error).toBe(true);
  }, 60_000);

  it("refuses a build as busy when the slot is taken and the queue is full", async () => {
    // Both calls start in one tick. The first takes the slot before its first
    // await, so the second meets a full queue of 0 at once.
    const first = compileArtifact(COMPONENT);
    const second = await compileArtifact(COMPONENT + "\n// two", { maxQueued: 0 });
    expect(second.ok).toBe(false);
    if (second.ok) return;
    expect(second.reason).toBe("busy");
    expect(second.error).toBe(BUSY);
    expect((await first).ok).toBe(true);
  });

  it("queues a build behind the slot, and runs it when the slot frees", async () => {
    const first = compileArtifact(COMPONENT);
    const second = compileArtifact(COMPONENT + "\n// queued");
    const [a, b] = await Promise.all([first, second]);
    expect(a.ok && b.ok).toBe(true);
  });
});

describe("BuildSlot", () => {
  it("hands the slot over in order, refuses past the queue, and frees it at the end", async () => {
    const slot = new BuildSlot();
    expect(await slot.acquire(1, 1_000)).toBe(true);
    const waiting = slot.acquire(1, 1_000);
    expect(slot.queued).toBe(1);
    expect(await slot.acquire(1, 1_000)).toBe(false);
    slot.release();
    expect(await waiting).toBe(true);
    slot.release();
    expect(await slot.acquire(0, 1_000)).toBe(true);
  });

  it("gives up a wait that runs out, and drops it from the queue", async () => {
    const slot = new BuildSlot();
    expect(await slot.acquire(1, 1_000)).toBe(true);
    expect(await slot.acquire(1, 20)).toBe(false);
    expect(slot.queued).toBe(0);
  });
});
