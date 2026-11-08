/**
 * compileArtifact — bundle an agent-authored React component into one
 * self-contained IIFE that can run inside the artifact sandbox.
 *
 * ── Why a bundler at all ───────────────────────────────────────────────────
 * Artifacts run in an opaque-origin iframe under `default-src 'none'`
 * (SandboxedHtml): no CDN, no imports, no network of any kind. So the runtime
 * has to be inlined with the component. React 19 dropped its UMD builds, so
 * there is no prebuilt file to drop in — the only way to get a React runtime
 * into that frame is to bundle it ourselves.
 *
 * ── Why Preact under the hood ──────────────────────────────────────────────
 * Agents write ordinary React (`import { useState } from "react"`). We alias
 * react/react-dom to preact/compat at bundle time, which is invisible to the
 * agent and 10x smaller: ~19 KB vs ~194 KB for the same source. That matters
 * because every inline chat card inlines its whole runtime into srcDoc. Since
 * the frame has no network, agents cannot pull third-party packages anyway, so
 * React's ecosystem advantage does not apply here. Swap RUNTIME_ALIAS to use
 * real React instead — nothing else in this file changes.
 *
 * ── Why the import allowlist is load-bearing ───────────────────────────────
 * This compiles UNTRUSTED, model-authored source on the server. Left alone,
 * esbuild would happily resolve `import x from "/etc/passwd"` or
 * "../../../secrets" and inline the bytes into output we hand back to the
 * client — a file-read primitive. The onResolve hook below therefore rejects
 * every specifier that is not an allowlisted runtime module, so no path ever
 * reaches the filesystem resolver. Bundling never executes the code, but it
 * does read it, and that is enough to matter. An import attribute
 * (`with { type: "text" }`) changes how esbuild loads an allowed file, so the
 * allowlist refuses every attribute too (task 2432, F3).
 *
 * ── Why the limits are load-bearing (task 2432, F1) ────────────────────────
 * Any signed-in member can send source here, and the box serves every tenant.
 * Measured on 2026-11-09 (STU-L0): one 256 KiB source drove the esbuild
 * process to 966 MB, and the minifier's time grows faster than its input
 * (nested ternaries: 16 KB in 1 s, 64 KB in 13 s, 256 KiB past 150 s). Some
 * shapes grow fast with no minifier too (nested arrows). So:
 *   • no minifier — the frame inlines the bundle, so bytes cost little;
 *   • ONE build at a time in this process, a short queue, and a refusal past it;
 *   • a timeout that KILLS the esbuild process (`stop()`). `cancel()` on a
 *     context is not enough: measured, it let a 3 s build run to its end;
 *   • a cap on the output, which a deep JSX tree inflates nine times over.
 * One build at a time is what makes `stop()` precise: the only build that the
 * kill ends is the one that ran out of time. The route adds a cap on the body
 * and a rate limit for each member (`app/api/artifacts/compile/route.ts`).
 */
import { existsSync } from "node:fs";
import path from "node:path";

import { build, stop, type BuildOptions, type BuildResult, type Plugin } from "esbuild";

/** The bare specifier artifacts import the design kit from. */
const UI_PACKAGE = "@cc/ui";

/**
 * Absolute path to the design-kit source, bundled into artifacts on demand.
 *
 * Resolved from cwd because the deploy builds and runs in the repo checkout
 * (`npm ci && npm run build && next start`), so src/ is present at runtime. If
 * that ever stops being true — a standalone/Docker output that ships only
 * .next — compilation fails loudly via assertUiKitPresent rather than silently
 * losing every component.
 */
const UI_SOURCE = path.join(process.cwd(), "src", "lib", "artifactUi.tsx");

/**
 * Bare specifiers an artifact module may import. Everything else is refused.
 *
 * The `preact/jsx-*` entries are not for agents — automatic JSX makes esbuild
 * inject `import { jsx } from "preact/jsx-runtime"` into the agent's own module,
 * so the allowlist has to admit the compiler's own import or nothing compiles.
 */
const ALLOWED_IMPORTS = new Set([
  "react",
  "react-dom",
  "react-dom/client",
  "react/jsx-runtime",
  "react/jsx-dev-runtime",
  "preact/jsx-runtime",
  "preact/jsx-dev-runtime",
  // The Metorite design kit as components (lib/artifactUi.tsx). Bundled
  // from our own source, tree-shaken, so an artifact pays only for what it uses.
  UI_PACKAGE,
]);

/** What an agent should see in a refusal — the injected internals are noise. */
const AGENT_FACING_IMPORTS = ["@cc/ui", "react", "react-dom/client"];

/**
 * react/react-dom → preact/compat, applied by esbuild's own `alias`.
 * Point these at "react"/"react-dom" to ship real React instead; nothing else
 * in this file needs to change.
 */
const RUNTIME_ALIAS: Record<string, string> = {
  "react": "preact/compat",
  "react-dom": "preact/compat",
  "react-dom/client": "preact/compat",
  // Subpaths need their own entries: aliasing "react" does not rewrite
  // "react/jsx-runtime" into anything that exists under preact/compat.
  "react/jsx-runtime": "preact/jsx-runtime",
  "react/jsx-dev-runtime": "preact/jsx-runtime",
};

/** The limits of one compile. Tests narrow them, the route takes the defaults. */
export interface CompileLimits {
  /** Refuse absurd inputs before handing them to the bundler. */
  maxSourceBytes: number;
  /** The largest bundle we hand back to the frame. */
  maxOutputBytes: number;
  /** A build that runs longer is stopped, and its esbuild process is killed. */
  timeoutMs: number;
  /** Builds that may wait for the one build slot of this process. */
  maxQueued: number;
  /** The longest a build waits for the slot before it is refused as busy. */
  maxWaitMs: number;
}

export const COMPILE_LIMITS: Readonly<CompileLimits> = Object.freeze({
  maxSourceBytes: 256 * 1024,
  maxOutputBytes: 1024 * 1024,
  timeoutMs: 10_000,
  maxQueued: 4,
  maxWaitMs: 10_000,
});

export interface CompileOk {
  ok: true;
  js: string;
  /** Bundle size in bytes — surfaced so callers can warn on heavy artifacts. */
  bytes: number;
}

/**
 * Why a compile failed. The route maps `busy` to 429. Every other reason is a
 * fault of the source, so the route answers it as data (200, `ok: false`).
 */
export type CompileFailure = "source" | "too_large" | "output_too_large" | "timeout" | "busy" | "setup";

export interface CompileErr {
  ok: false;
  /** Agent-readable diagnostics, newline separated. */
  error: string;
  reason: CompileFailure;
}

export type CompileResult = CompileOk | CompileErr;

/**
 * Reject any import from the agent's module that is not an allowlisted runtime.
 *
 * Only imports whose *importer* is the agent's own virtual module are policed —
 * identified by the "artifact" namespace. Imports that originate inside
 * node_modules (Preact's internals resolving each other, including relative
 * paths) are left to esbuild.
 *
 * This hook never resolves anything; it returns either an error or null. That is
 * deliberate: calling `pluginBuild.resolve()` from an onResolve with a catch-all
 * filter re-enters this same hook and deadlocks the build. Rewriting
 * react → preact/compat is left to esbuild's built-in `alias`, which runs after
 * plugins decline.
 */
function allowlistPlugin(): Plugin {
  return {
    name: "artifact-import-allowlist",
    setup(pluginBuild) {
      pluginBuild.onResolve({ filter: /.*/ }, (args) => {
        // Not the agent's code (our entry, or a runtime file resolving its own
        // internals) — esbuild handles it.
        if (args.namespace !== "artifact") return null;
        // `with { type: "text" }` loads an allowed file as text, so an
        // attribute widens what the allowlist admits. No artifact needs one.
        if (args.with && Object.keys(args.with).length > 0) {
          return {
            errors: [{
              text:
                `Cannot import ${JSON.stringify(args.path)} with an import attribute ` +
                `(\`with { … }\`). Remove the \`with { … }\` part of the import.`,
            }],
          };
        }
        if (ALLOWED_IMPORTS.has(args.path)) return null;
        // Kept to one line on purpose. A file with many bad imports repeats
        // this per import, so the long "why" belongs in the docs, not stapled
        // to every diagnostic.
        return {
          errors: [{
            text:
              `Cannot import ${JSON.stringify(args.path)} — artifacts run offline, so ` +
              `only ${AGENT_FACING_IMPORTS.join(", ")} are available. Inline it instead.`,
          }],
        };
      });
    },
  };
}

/**
 * How many diagnostics to hand back. One real mistake often cascades into
 * dozens; the agent only needs enough to find it, and every extra line is
 * tokens spent twice — once reading the error, once re-emitting the artifact.
 */
const MAX_REPORTED_ERRORS = 8;

/** Format esbuild diagnostics into something an agent can act on, and no more. */
function formatErrors(err: unknown): string {
  const messages = (err as { errors?: { text?: string; location?: { line?: number } | null }[] })
    ?.errors;
  if (!Array.isArray(messages) || !messages.length) {
    return err instanceof Error ? err.message : String(err);
  }

  // Collapse repeats before truncating: 60 bad imports produce 60 copies of the
  // same sentence, which is noise, not information.
  const seen = new Map<string, { line?: number; count: number }>();
  for (const m of messages) {
    const text = m.text ?? "";
    const prior = seen.get(text);
    if (prior) prior.count += 1;
    else seen.set(text, { line: m.location?.line, count: 1 });
  }

  const lines = [...seen.entries()]
    .slice(0, MAX_REPORTED_ERRORS)
    .map(([text, { line, count }]) => {
      const where = line ? `line ${line}: ` : "";
      const repeats = count > 1 ? ` (×${count})` : "";
      return `${where}${text}${repeats}`;
    });

  if (seen.size > MAX_REPORTED_ERRORS) {
    lines.push(`… and ${seen.size - MAX_REPORTED_ERRORS} more distinct errors`);
  }
  return lines.join("\n");
}

// ── One build at a time ──────────────────────────────────────────────────────

/**
 * The one build slot of this process, with a short FIFO queue.
 *
 * One slot, not two: a build that runs out of time is ended with `stop()`,
 * which kills the whole esbuild service process. With one slot, that process
 * holds no other build, so the kill ends only the build that timed out.
 */
export class BuildSlot {
  private busy = false;
  private readonly waiting: Array<(granted: boolean) => void> = [];

  /** Builds that wait for the slot now. */
  get queued(): number {
    return this.waiting.length;
  }

  /** True when the caller holds the slot. False when the queue is full, or the wait ran out. */
  acquire(maxQueued: number, maxWaitMs: number): Promise<boolean> {
    if (!this.busy) {
      this.busy = true;
      return Promise.resolve(true);
    }
    if (this.waiting.length >= maxQueued) return Promise.resolve(false);
    return new Promise<boolean>((resolve) => {
      const grant = (granted: boolean) => {
        clearTimeout(timer);
        resolve(granted);
      };
      const timer = setTimeout(() => {
        const at = this.waiting.indexOf(grant);
        if (at >= 0) this.waiting.splice(at, 1);
        resolve(false);
      }, maxWaitMs);
      this.waiting.push(grant);
    });
  }

  /** Hand the slot to the next waiter, or free it. */
  release(): void {
    const next = this.waiting.shift();
    if (next) next(true);
    else this.busy = false;
  }
}

const buildSlot = new BuildSlot();

const TIMED_OUT = Symbol("timed out");

/**
 * Run one esbuild build, and KILL it when it runs out of time.
 *
 * `stop()` kills the esbuild service process, so the work really ends. The
 * next build starts a fresh process. The promise of the killed build never
 * settles, or rejects later, so it gets a no-op catch and nobody waits on it.
 */
async function buildWithin(
  options: BuildOptions & { write: false },
  timeoutMs: number,
): Promise<BuildResult<BuildOptions & { write: false }> | typeof TIMED_OUT> {
  const work = build(options);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<typeof TIMED_OUT>((resolve) => {
    timer = setTimeout(() => resolve(TIMED_OUT), timeoutMs);
  });
  try {
    const first = await Promise.race([work, deadline]);
    if (first === TIMED_OUT) {
      work.catch(() => undefined);
      await stop();
    }
    return first;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Compile an agent-authored React component to a self-contained IIFE.
 *
 * `code` must default-export a component. The returned JS mounts it into
 * `#root` and expects nothing else from the page. `overrides` narrows the
 * limits, for tests only. The route passes none.
 */
export async function compileArtifact(
  code: string,
  overrides: Partial<CompileLimits> = {},
): Promise<CompileResult> {
  const limits: CompileLimits = { ...COMPILE_LIMITS, ...overrides };
  if (!code || !code.trim()) {
    return {
      ok: false,
      reason: "source",
      error: "No source code arrived. Send the component's source and try again.",
    };
  }
  if (Buffer.byteLength(code, "utf8") > limits.maxSourceBytes) {
    return {
      ok: false,
      reason: "too_large",
      error: `Source is larger than ${limits.maxSourceBytes / 1024} KB. Split the artifact up or trim it.`,
    };
  }
  if (code.includes(UI_PACKAGE) && !existsSync(UI_SOURCE)) {
    // Deployment problem, not an agent mistake — say so plainly rather than
    // letting it surface as a confusing "cannot import" further down.
    return {
      ok: false,
      reason: "setup",
      error:
        `The ${UI_PACKAGE} design kit is not available on this server ` +
        `(expected ${UI_SOURCE}). This is a deployment issue — the artifact ` +
        `source is fine.`,
    };
  }

  // The first await. Everything above ran in the caller's tick, so a second
  // call made in the same tick already sees this one holding the slot.
  if (!(await buildSlot.acquire(limits.maxQueued, limits.maxWaitMs))) {
    return { ok: false, reason: "busy", error: BUSY };
  }
  try {
    return await compileInSlot(code, limits);
  } finally {
    buildSlot.release();
  }
}

/** The refusal of a full queue. The route answers it with 429. */
export const BUSY =
  "Metorite is building other artifacts right now. Wait a few seconds and try again.";

function timedOut(limits: CompileLimits): CompileErr {
  return {
    ok: false,
    reason: "timeout",
    error:
      `Building the artifact took longer than ${limits.timeoutMs / 1000} seconds, so ` +
      `Metorite stopped it. Simplify the component, for example with fewer deeply ` +
      `nested expressions, and send it again.`,
  };
}

async function compileInSlot(code: string, limits: CompileLimits): Promise<CompileResult> {
  // The agent supplies a component module; we own the mount so the agent never
  // has to know about the root element or which renderer is underneath.
  const entry = [
    'import Artifact from "artifact:component";',
    'import { render } from "preact";',
    'import { h } from "preact";',
    'const el = document.getElementById("root");',
    "render(h(Artifact, {}), el);",
  ].join("\n");

  const options: BuildOptions & { write: false } = {
    stdin: {
      contents: entry,
      resolveDir: process.cwd(),
      sourcefile: "artifact-entry.jsx",
      loader: "jsx",
    },
    bundle: true,
    // No minifier (task 2432). Its time grows faster than the source, and a
    // build of 64 KB took 13 s with it. The frame inlines the bundle, so the
    // extra bytes cost little.
    minify: false,
    format: "iife",
    target: ["es2020"],
    jsx: "automatic",
    jsxImportSource: "preact",
    // Rewrites the agent's `from "react"` to the runtime we actually ship,
    // and points @cc/ui at our design-kit source. Runs after plugins decline,
    // so the allowlist above still gets first say.
    alias: { ...RUNTIME_ALIAS, [UI_PACKAGE]: UI_SOURCE },
    write: false,
    logLevel: "silent",
    legalComments: "none",
    // Keep the bundle honest: no process.env leakage into artifact code.
    define: { "process.env.NODE_ENV": '"production"' },
    plugins: [
      {
        name: "artifact-virtual-entry",
        setup(pluginBuild) {
          // The agent's own module, supplied in memory — never read from disk.
          pluginBuild.onResolve({ filter: /^artifact:component$/ }, () => ({
            path: "artifact:component",
            namespace: "artifact",
          }));
          pluginBuild.onLoad({ filter: /.*/, namespace: "artifact" }, () => ({
            contents: code,
            loader: "tsx",
            resolveDir: process.cwd(),
          }));
        },
      },
      allowlistPlugin(),
    ],
  };

  try {
    const result = await buildWithin(options, limits.timeoutMs);
    if (result === TIMED_OUT) return timedOut(limits);
    const out = result.outputFiles?.[0]?.text ?? "";
    if (!out) {
      return {
        ok: false,
        reason: "source",
        error:
          "The bundler produced no output. Check that the source default-exports a " +
          "component, and send it again.",
      };
    }
    const bytes = Buffer.byteLength(out, "utf8");
    if (bytes > limits.maxOutputBytes) {
      return {
        ok: false,
        reason: "output_too_large",
        error:
          `The built artifact is ${Math.ceil(bytes / 1024)} KB, which is more than the ` +
          `${limits.maxOutputBytes / 1024} KB limit. Make the component smaller, for ` +
          `example with a loop over data instead of repeated markup.`,
      };
    }
    return { ok: true, js: out, bytes };
  } catch (err) {
    return { ok: false, reason: "source", error: formatErrors(err) };
  }
}
