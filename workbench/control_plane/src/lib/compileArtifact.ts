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
 * does read it, and that is enough to matter.
 */
import { existsSync } from "node:fs";
import path from "node:path";

import { build, type Plugin } from "esbuild";

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

/** Refuse absurd inputs before handing them to the bundler. */
const MAX_SOURCE_BYTES = 256 * 1024;

export interface CompileOk {
  ok: true;
  js: string;
  /** Bundle size in bytes — surfaced so callers can warn on heavy artifacts. */
  bytes: number;
}

export interface CompileErr {
  ok: false;
  /** Agent-readable diagnostics, newline separated. */
  error: string;
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

/**
 * Compile an agent-authored React component to a self-contained IIFE.
 *
 * `code` must default-export a component. The returned JS mounts it into
 * `#root` and expects nothing else from the page.
 */
export async function compileArtifact(code: string): Promise<CompileResult> {
  if (!code || !code.trim()) {
    return { ok: false, error: "No source code was provided." };
  }
  if (Buffer.byteLength(code, "utf8") > MAX_SOURCE_BYTES) {
    return {
      ok: false,
      error: `Source is larger than ${MAX_SOURCE_BYTES / 1024} KB. Split the artifact up or trim it.`,
    };
  }
  if (code.includes(UI_PACKAGE) && !existsSync(UI_SOURCE)) {
    // Deployment problem, not an agent mistake — say so plainly rather than
    // letting it surface as a confusing "cannot import" further down.
    return {
      ok: false,
      error:
        `The ${UI_PACKAGE} design kit is not available on this server ` +
        `(expected ${UI_SOURCE}). This is a deployment issue — the artifact ` +
        `source is fine.`,
    };
  }

  // The agent supplies a component module; we own the mount so the agent never
  // has to know about the root element or which renderer is underneath.
  const entry = [
    'import Artifact from "artifact:component";',
    'import { render } from "preact";',
    'import { h } from "preact";',
    'const el = document.getElementById("root");',
    "render(h(Artifact, {}), el);",
  ].join("\n");

  try {
    const result = await build({
      stdin: {
        contents: entry,
        resolveDir: process.cwd(),
        sourcefile: "artifact-entry.jsx",
        loader: "jsx",
      },
      bundle: true,
      minify: true,
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
    });

    const out = result.outputFiles?.[0]?.text ?? "";
    if (!out) return { ok: false, error: "The bundler produced no output." };
    return { ok: true, js: out, bytes: Buffer.byteLength(out, "utf8") };
  } catch (err) {
    return { ok: false, error: formatErrors(err) };
  }
}
