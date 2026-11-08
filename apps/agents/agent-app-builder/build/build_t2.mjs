#!/usr/bin/env node
// App Workshop · T2 (React) build — platform-owned, not authored by the
// builder agent (docs/app-workshop/README.md §4.1/§9). Bundles a T2 app's
// `src/main.tsx` and injects the result into the workspace's `index.html`
// template, producing `dist/bundle.html` — the file `entry` points at and
// every existing publish/preview/durability path already reads dynamically.
//
// Two dependency modes, auto-detected per workspace:
//   - Default (no app-local `node_modules`): resolve react/react-dom/
//     lucide-react/esbuild from the shared, deploy-provisioned vendor cache;
//     an import allowlist blocks anything else. Zero per-app install/disk
//     cost — most T2 apps stay on this path.
//   - Custom deps (app-local `node_modules` present, via
//     `install_t2_deps.mjs`): standard npm resolution rooted at the
//     workspace, no allowlist — the app can import anything it explicitly
//     installed (e.g. three.js for a 3D viewer). esbuild itself still comes
//     from the vendor cache either way; only runtime deps move per-app.
//
// `@cc/ui` (workbench/control_plane/src/lib/artifactUi.tsx) is importable
// alongside react/react-dom — the same 25-component design-kit the chat
// "artifacts" system (lib/compileArtifact.ts) bundles into React artifacts,
// aliased in here the same way so a T2 app can write <Stat/>/<Table/> instead
// of hand-rolled cc-* divs. That file is pure JSX + prop types (no
// React-vs-Preact-specific runtime code — its one `react` import is
// type-only and erased at compile time), so it bundles unchanged under real
// React here despite compileArtifact.ts aliasing react -> preact for its own,
// separate (ephemeral, inline-in-chat) use case. `Submit`/`Action` are the
// exception: they call `window.ccSubmit`/`window.ccAction`, chat-artifact
// bridge globals that don't exist in a Custom App's frame — harmless no-ops
// here, but pointless; use `cc.storage`/`cc.tools` for real interactions.
//
// In default mode, an import allowlist (react/react-dom/@cc/ui only, policed
// for imports made BY the app's own src/ files — react/react-dom/@cc/ui's own
// internal resolution is left alone) mirrors compileArtifact.ts's
// defense-in-depth: the vendor cache having only those packages installed
// already prevents resolving anything else, but an explicit allowlist
// doesn't silently widen if something else is ever added to that shared
// directory. In custom-deps mode the allowlist is skipped entirely — the app
// can only import what it explicitly `npm install`ed, which is its own gate.
//
// Usage: node build_t2.mjs <workspace-dir>
// Exit 0 + byte size on stdout on success; exit 1 + esbuild diagnostics on
// stderr on failure. Never touches dist/bundle.html on a failed build — the
// last-known-good bundle keeps serving until the next successful build.

import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const BUNDLE_MARKER = "<!-- CC_T2_BUNDLE -->";
const UI_PACKAGE = "@cc/ui";

// apps/agents/agent-app-builder/build/build_t2.mjs -> repo root (5 levels up:
// strip the filename, then build/ -> agent-app-builder/ -> agents/ -> apps/).
const REPO_ROOT = resolve(fileURLToPath(import.meta.url), "..", "..", "..", "..", "..");
// WS-43h: in the sandbox, the broker mounts a copy of these scripts with no
// repo around them, and names the copy of artifactUi.tsx in
// CUSTOM_APPS_T2_UI_SOURCE. With the variable unset, the path in the repo.
const UI_SOURCE =
  (process.env.CUSTOM_APPS_T2_UI_SOURCE || "").trim() ||
  join(REPO_ROOT, "workbench", "control_plane", "src", "lib", "artifactUi.tsx");
const uiAvailable = existsSync(UI_SOURCE);

const ALLOWED_IMPORTS = new Set([
  "react",
  "react-dom",
  "react-dom/client",
  "react/jsx-runtime",
  "react/jsx-dev-runtime",
  // Real icon components — same library workbench/control_plane itself uses
  // (pinned to the same version in the vendor cache, see deploy.yml) — so a
  // T2 app never needs to hand-roll SVGs or reach for emoji as iconography.
  "lucide-react",
  ...(uiAvailable ? [UI_PACKAGE] : []),
]);

function vendorDir() {
  const override = (process.env.CUSTOM_APPS_T2_VENDOR_DIR || "").trim();
  if (override) return override;
  const clone = (process.env.AGENTS_CLONE_DIR || "").trim() || join(homedir(), ".acb", "agents");
  return join(clone, "vendor", "t2-react");
}

function fail(message) {
  process.stderr.write(`build_t2: ${message}\n`);
  process.exit(1);
}

/** Reject an app src/ file importing anything outside the allowlist. Only
 * imports made BY the app's own code are policed — react/react-dom/@cc/ui's
 * own internal resolution (importer outside src/) is left to esbuild. */
function allowlistPlugin(srcDir) {
  return {
    name: "t2-import-allowlist",
    setup(pluginBuild) {
      pluginBuild.onResolve({ filter: /.*/ }, (args) => {
        if (!args.importer) return null; // entry point resolution — not policed
        const rel = relative(srcDir, args.importer);
        const importerInsideSrc = !rel.startsWith("..") && !isAbsolute(rel);
        if (!importerInsideSrc) return null;
        if (args.path.startsWith("./") || args.path.startsWith("../")) return null;
        if (ALLOWED_IMPORTS.has(args.path)) return null;
        return {
          errors: [{
            text:
              `Cannot import ${JSON.stringify(args.path)}. T2 apps run offline in a sandbox ` +
              `with no network — only ${[...ALLOWED_IMPORTS].join(", ")} are importable. ` +
              `Inline any helper code into your own src/ files instead.`,
          }],
        };
      });
    },
  };
}

const workspace = resolve(process.argv[2] || "");
if (!workspace || !existsSync(workspace)) {
  fail(`workspace not found: ${workspace || "(none given)"}`);
}

const entryPoint = join(workspace, "src", "main.tsx");
if (!existsSync(entryPoint)) {
  fail(`missing entry point: src/main.tsx`);
}

const templatePath = join(workspace, "index.html");
if (!existsSync(templatePath)) {
  fail(`missing build template: index.html (must contain ${BUNDLE_MARKER})`);
}
const template = readFileSync(templatePath, "utf-8");
if (!template.includes(BUNDLE_MARKER)) {
  fail(`index.html is missing the ${BUNDLE_MARKER} marker`);
}

const vendor = vendorDir();
const vendorModules = join(vendor, "node_modules");
if (!existsSync(join(vendorModules, "esbuild"))) {
  fail(
    `esbuild not found in vendor cache (${vendorModules}) — ` +
      `has the T2 vendor cache been provisioned (deploy.yml)?`,
  );
}

// esbuild itself is always the vendored copy (a build tool, not a runtime
// dep). Whether react/react-dom/etc. resolve from the vendor cache or the
// app's own npm-installed node_modules depends on which the app has.
const hasAppDeps = existsSync(join(workspace, "node_modules"));

const require = createRequire(import.meta.url);
let esbuild;
try {
  esbuild = require(join(vendorModules, "esbuild"));
} catch (err) {
  fail(`failed to load esbuild from vendor cache: ${err.message}`);
}

let result;
try {
  // The async build() API, not buildSync() — buildSync cannot run plugins
  // (the import allowlist above), and top-level await is native to ESM.
  result = await esbuild.build({
    entryPoints: [entryPoint],
    bundle: true,
    write: false,
    format: "iife",
    platform: "browser",
    jsx: "automatic",
    target: ["es2020"],
    minify: true,
    legalComments: "none",
    logLevel: "silent",
    // Required: React's bundled code reads process.env.NODE_ENV at runtime.
    // Without this it throws "process is not defined" in the sandboxed
    // browser frame (no Node globals there) — also dead-code-eliminates
    // most React dev-mode diagnostic strings, keeping the bundle small.
    define: { "process.env.NODE_ENV": '"production"' },
    // Default mode: resolve react/react-dom against the shared vendor cache,
    // policed by the allowlist plugin. Custom-deps mode: standard resolution
    // rooted at the workspace's own node_modules (esbuild's default
    // algorithm — no nodePaths override needed), no allowlist.
    nodePaths: hasAppDeps ? [] : [vendorModules],
    alias: uiAvailable ? { [UI_PACKAGE]: UI_SOURCE } : {},
    plugins: hasAppDeps ? [] : [allowlistPlugin(join(workspace, "src"))],
  });
} catch (err) {
  const diagnostics = (err.errors || [])
    .map((e) => `  ${e.text}${e.location ? ` (${e.location.file}:${e.location.line})` : ""}`)
    .join("\n");
  fail(`esbuild failed:\n${diagnostics || err.message}`);
}

const outputFiles = result.outputFiles || [];
if (outputFiles.length === 0) {
  fail("esbuild produced no output");
}
const bundledJs = outputFiles[0].text;
if (!bundledJs || !bundledJs.trim()) {
  fail("esbuild produced an empty bundle");
}

const finalHtml = template.replace(BUNDLE_MARKER, `<script>${bundledJs}</script>`);

const distDir = join(workspace, "dist");
mkdirSync(distDir, { recursive: true });
const finalPath = join(distDir, "bundle.html");
const tmpPath = join(distDir, ".bundle.html.tmp");

try {
  writeFileSync(tmpPath, finalHtml, "utf-8");
  renameSync(tmpPath, finalPath);
} catch (err) {
  try {
    rmSync(tmpPath, { force: true });
  } catch {
    // best-effort cleanup only
  }
  fail(`failed to write dist/bundle.html: ${err.message}`);
}

process.stdout.write(
  `build_t2: ok, dist/bundle.html (${finalHtml.length} bytes, ` +
    `${hasAppDeps ? "custom deps" : "vendor cache"})\n`,
);
