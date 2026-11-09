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
// for imports made BY the app's own files) mirrors compileArtifact.ts's
// defense-in-depth: the vendor cache having only those packages installed
// already prevents resolving anything else, but an explicit allowlist
// doesn't silently widen if something else is ever added to that shared
// directory. In custom-deps mode the allowlist is skipped entirely — the app
// can only import what it explicitly `npm install`ed, which is its own gate.
//
// Containment (task 2464), in BOTH modes. Every app dir is a sibling under
// one apps root, across orgs, and esbuild follows an import anywhere on
// disk. One plugin (containmentPlugin) holds the one check, in two hooks:
//   - onResolve: a file of the app (any file inside the app dir, not only
//     src/, and the entry point) may import by a relative or absolute path
//     only inside the app dir. A file of the vendor cache may do so only
//     inside the vendor cache. A file: URL, and an importer outside the
//     roots, are refused. The allowlist (default mode) is checked here.
//   - onLoad: the REAL path (links followed) of each file that esbuild
//     loads must stay in the root of the path that esbuild found. So a
//     link in the app dir that points out is refused, and so is a package
//     or tsconfig mapping that leads out. preserveSymlinks keeps the found
//     path, so the two paths can differ.
//   - onLoad also reads each code file of the app before esbuild parses it,
//     and refuses a require() or import() whose path is built from parts
//     (`+` or `${}`). esbuild turns such a call into a glob, and sends each
//     match straight to onLoad.
//   - A refusal of a file outside every root names no path, and the error
//     printer prints each line once. A diagnostic placed in a file outside
//     the roots shows only the fixed text (review round 3).
//   - A bare name with a `.` or `..` part, and `@cc/ui/<part>`, are refused.
// The roots are the app dir, the vendor cache and the one @cc/ui file.
// The template (index.html) and the output dir (dist/) must also be real
// paths inside the app dir. The output path is fixed here, and app.json
// never names it. Fence: tests/unit/test_app_builder_t2_containment.py.
//
// Usage: node build_t2.mjs <workspace-dir>
// Exit 0 + byte size on stdout on success; exit 1 + esbuild diagnostics on
// stderr on failure. Never touches dist/bundle.html on a failed build — the
// last-known-good bundle keeps serving until the next successful build.

import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { isAbsolute, join, posix, relative, resolve, sep, win32 } from "node:path";
import { fileURLToPath } from "node:url";

const BUNDLE_MARKER = "<!-- CC_T2_BUNDLE -->";
const UI_PACKAGE = "@cc/ui";
const OWN_FILES_ONLY = "An app can import only its own files.";
// The plugin's own pluginBuild.resolve() call carries this key, so the plugin
// lets that call through to esbuild's resolver instead of recursing.
const CONTAIN_GUARD = "t2ContainResolving";

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

/** The real path of *p* (every link followed), or null when it has none. */
function realOrNull(p) {
  try {
    return realpathSync.native(p);
  } catch {
    return null;
  }
}

/** True when the path *target* is the path *root* or below it. Both are
 * absolute, and both are found paths or both are real paths. */
function within(root, target) {
  if (!root || !target) return false;
  const rel = relative(root, target);
  return rel === "" || (!isAbsolute(rel) && rel !== ".." && !rel.startsWith(`..${sep}`));
}

/** `.`, `..`, or a path that starts with `./`, `../`, `.\` or `..\`. */
function isRelativeImport(spec) {
  return /^\.\.?(?:[\\/]|$)/.test(spec);
}

/** An absolute path on either OS (`/etc/x`, `C:\x`, `\\host\x`). */
function isAbsoluteImport(spec) {
  return posix.isAbsolute(spec) || win32.isAbsolute(spec);
}

function refuse(spec) {
  return { errors: [{ text: `Cannot import ${JSON.stringify(spec)}. ${OWN_FILES_ONLY}` }] };
}

/** The refusal of a file outside every root. It names NO path: esbuild sends
 * each file that a glob import matches straight to onLoad, so a path here
 * would list the files of other apps (task 2464 review P0). */
function refuseUnnamed() {
  return { errors: [{ text: OWN_FILES_ONLY }] };
}

// The files that esbuild parses as code. Only these can hold a glob import.
const CODE_FILE = /\.(?:[cm]?[jt]sx?)$/i;
const CALL_START = /\b(?:require|import)\s*(?:\/\*[\s\S]*?\*\/\s*)*\(/g;

/** The end index of the string or template literal that starts at *i*. */
function skipLiteral(source, i) {
  const quote = source[i];
  let j = i + 1;
  while (j < source.length) {
    const c = source[j];
    if (c === "\\") { j += 2; continue; }
    if (c === quote) return j + 1;
    if (quote === "`" && c === "$" && source[j + 1] === "{") {
      let depth = 1;
      j += 2;
      while (j < source.length && depth > 0) {
        if (source[j] === "{") depth++;
        else if (source[j] === "}") depth--;
        j++;
      }
      continue;
    }
    j++;
  }
  return j;
}

/** True when *source* holds a require() or import() whose argument is built
 * from parts: a literal joined with `+`, or a template literal with `${`.
 * esbuild turns such a call into a glob, reads the matching dir and sends each
 * match to onLoad. So the build refuses the file before esbuild parses it
 * (task 2464 review P0). A call with one fixed literal, or with no literal
 * at all (`import(x)`), stays legal. */
function hasGlobImport(source) {
  CALL_START.lastIndex = 0;
  let m;
  while ((m = CALL_START.exec(source))) {
    let i = m.index + m[0].length;
    let depth = 1;
    let literals = 0;
    let joined = false;
    while (i < source.length && depth > 0) {
      const c = source[i];
      if (c === '"' || c === "'" || c === "`") {
        const end = skipLiteral(source, i);
        literals++;
        if (c === "`" && source.slice(i, end).includes("${")) joined = true;
        i = end;
        continue;
      }
      if (c === "/" && source[i + 1] === "*") {
        const end = source.indexOf("*/", i + 2);
        i = end < 0 ? source.length : end + 2;
        continue;
      }
      if (c === "/" && source[i + 1] === "/") {
        const end = source.indexOf("\n", i);
        i = end < 0 ? source.length : end;
        continue;
      }
      if (c === "(") depth++;
      else if (c === ")") depth--;
      else if (c === "+") joined = true;
      i++;
    }
    if (literals > 0 && joined) return true;
  }
  return false;
}

/** The one containment check of every import, in both dependency modes
 * (task 2464, see the header). `app` and `vendor` are { dir, real }: the
 * path that esbuild finds and its real path. `ui` is the same for the one
 * @cc/ui file, or null. `allowlist` is the set of bare package names in
 * default mode, and null in custom-deps mode. */
function containmentPlugin({ app, vendor, ui, allowlist }) {
  /** The root that the found path *p* is in: "app", "vendor", "ui" or null. */
  function rootOf(p) {
    if (!p) return null;
    if (within(app.dir, p)) return "app";
    if (within(vendor.dir, p)) return "vendor";
    if (ui && relative(ui.dir, p) === "") return "ui";
    return null;
  }

  return {
    name: "t2-import-containment",
    setup(pluginBuild) {
      pluginBuild.onResolve({ filter: /.*/ }, async (args) => {
        if (args.pluginData && args.pluginData[CONTAIN_GUARD]) return undefined;
        const spec = args.path;
        // The importer is a found path. onLoad below already checked it.
        const from = args.kind === "entry-point" ? "app" : rootOf(args.importer);
        if (from === null || /^file:/i.test(spec)) return refuse(spec);
        if (isRelativeImport(spec) || isAbsoluteImport(spec)) {
          // A path import stays in the importer's own root. A link on the
          // way is onLoad's check. esbuild may drop a `?` or `#` suffix and
          // try again, so the path with no suffix must stay in the root too.
          const own = from === "app" ? app.dir : from === "vendor" ? vendor.dir : null;
          const stays = [spec, spec.split(/[?#]/)[0]].every((p) =>
            within(own, isRelativeImport(p) ? resolve(args.resolveDir, p) : resolve(p)));
          return stays ? undefined : refuse(spec);
        }
        // A bare package name. A `.` or `..` part walks out of node_modules
        // (`x/../../other-app`), and `@cc/ui/<part>` walks out of the one UI
        // file through the alias. esbuild reads a package.json on the way, and
        // its parse error showed the first word (task 2464 review P3).
        if (from === "app") {
          const parts = spec.split(/[\\/]/);
          if (parts.includes(".") || parts.includes("..") || spec.startsWith(`${UI_PACKAGE}/`)) {
            return refuse(spec);
          }
        }
        if (from === "app" && allowlist && !allowlist.has(spec)) {
          return {
            errors: [{
              text:
                `Cannot import ${JSON.stringify(spec)}. T2 apps run offline in a sandbox ` +
                `with no network — only ${[...allowlist].join(", ")} are importable. ` +
                `Inline any helper code into your own src/ files instead.`,
            }],
          };
        }
        if (from !== "ui") return undefined;
        // @cc/ui's own imports (react/jsx-runtime) resolve as if the app
        // made them, from the app dir. Its own dir is outside every root.
        const found = await pluginBuild.resolve(spec, {
          kind: args.kind,
          importer: args.importer,
          resolveDir: app.dir,
          pluginData: { [CONTAIN_GUARD]: true },
        });
        if (found.errors.length > 0) return { errors: found.errors, warnings: found.warnings };
        return {
          path: found.path,
          namespace: found.namespace,
          external: found.external,
          suffix: found.suffix,
          sideEffects: found.sideEffects,
          pluginData: found.pluginData,
        };
      });

      // Every file that esbuild reads from disk: its real path must stay in
      // the root of the path that esbuild found (a link that points out of
      // the app dir is refused here, before esbuild reads the file).
      pluginBuild.onLoad({ filter: /.*/, namespace: "file" }, (args) => {
        const root = rootOf(args.path);
        const real = realOrNull(args.path);
        if (root === "app" && within(app.real, real)) {
          // A file of the app that builds a path from parts is refused here,
          // before esbuild parses it and globs a dir (review P0).
          if (CODE_FILE.test(args.path) && hasGlobImport(readFileSync(real, "utf-8"))) {
            const shown = relative(app.dir, args.path).split(sep).join("/");
            return {
              errors: [{
                text:
                  `${shown} builds an import path from parts (with + or \${}). ` +
                  `${OWN_FILES_ONLY} Import each file by its fixed path.`,
              }],
            };
          }
          return undefined;
        }
        if (root === "vendor" && within(vendor.real, real)) return undefined;
        if (root === "ui" && real !== null && real === ui.real) return undefined;
        // A path in the app dir that leads out (a link) names only the app's
        // own path. Any other path is named by no word at all.
        return root === "app" ? refuse(relative(app.dir, args.path).split(sep).join("/")) : refuseUnnamed();
      });
    },
  };
}

const workspace = resolve(process.argv[2] || "");
if (!workspace || !existsSync(workspace)) {
  fail(`workspace not found: ${workspace || "(none given)"}`);
}
// Every containment check compares real paths with the app dir's real path.
const appRoot = realOrNull(workspace);
if (!appRoot) {
  fail(`workspace not found: ${workspace}`);
}

const entryPoint = join(workspace, "src", "main.tsx");
if (!existsSync(entryPoint)) {
  fail(`missing entry point: src/main.tsx`);
}

const templatePath = join(workspace, "index.html");
if (!existsSync(templatePath)) {
  fail(`missing build template: index.html (must contain ${BUNDLE_MARKER})`);
}
// A link at index.html would put another app's file into this bundle.
const templateReal = realOrNull(templatePath);
if (!within(appRoot, templateReal)) {
  fail(`index.html is not a file of this app. ${OWN_FILES_ONLY} Remove the link, then build again.`);
}
const template = readFileSync(templateReal, "utf-8");
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
    // under the allowlist. Custom-deps mode: standard resolution rooted at
    // the workspace's own node_modules (esbuild's default algorithm — no
    // nodePaths override needed), no allowlist. The containment plugin runs
    // in both modes.
    nodePaths: hasAppDeps ? [] : [vendorModules],
    alias: uiAvailable ? { [UI_PACKAGE]: UI_SOURCE } : {},
    // Keep the path that esbuild found, with any link in it, so the
    // plugin's onLoad can compare it with the real path.
    preserveSymlinks: true,
    // Read no tsconfig.json or jsconfig.json from disk (task 2464). Their
    // "extends" made esbuild read a file of another app, and its parse error
    // showed that file's first word. The options above set what a T2 app needs.
    tsconfigRaw: {},
    plugins: [
      containmentPlugin({
        app: { dir: workspace, real: appRoot },
        vendor: { dir: resolve(vendor), real: realOrNull(vendor) },
        ui: uiAvailable ? { dir: resolve(UI_SOURCE), real: realOrNull(UI_SOURCE) } : null,
        allowlist: hasAppDeps ? null : ALLOWED_IMPORTS,
      }),
    ],
  });
} catch (err) {
  // A diagnostic that esbuild places in a file outside the roots (a parse
  // error in another app's package.json) shows the fixed text, never its
  // words or its path. Each line prints once, so a count says nothing.
  const vendorDirFound = resolve(vendor);
  const uiFound = uiAvailable ? resolve(UI_SOURCE) : null;
  const ownFile = (file) => {
    const found = resolve(workspace, file);
    return within(workspace, found) || within(vendorDirFound, found) ||
      (uiFound !== null && relative(uiFound, found) === "");
  };
  const lines = (err.errors || []).map((e) => {
    const file = e.location && e.location.file;
    if (file && !ownFile(file)) return `  ${OWN_FILES_ONLY}`;
    return `  ${e.text}${file ? ` (${file}:${e.location.line})` : ""}`;
  });
  const diagnostics = [...new Set(lines)].join("\n");
  fail(`esbuild failed:\n${diagnostics || "the build stopped with no diagnostic."}`);
}

const outputFiles = result.outputFiles || [];
if (outputFiles.length === 0) {
  fail("esbuild produced no output");
}
const bundledJs = outputFiles[0].text;
if (!bundledJs || !bundledJs.trim()) {
  fail("esbuild produced an empty bundle");
}

// A function, not a string: String.replace reads "$&" and "$1" in a string
// as patterns, and React's own code holds "$&/". The string form put the
// marker into the bundle and broke keyed children (WS-43h docker test).
const finalHtml = template.replace(BUNDLE_MARKER, () => `<script>${bundledJs}</script>`);

// dist/ must be a real dir inside the app dir. A link at dist/ would write
// this bundle into another app's dir.
try {
  mkdirSync(join(appRoot, "dist"), { recursive: true });
} catch (err) {
  fail(`failed to make dist/: ${err.message}`);
}
const distDir = realOrNull(join(appRoot, "dist"));
if (!distDir || distDir === appRoot || !within(appRoot, distDir)) {
  fail("dist/ is not a dir of this app. Remove the link at dist/, then build again.");
}
const finalPath = join(distDir, "bundle.html");
const tmpPath = join(distDir, ".bundle.html.tmp");

try {
  // rmSync removes a planted link itself, never its target. The "wx" flag
  // (O_EXCL) never writes through a link, and renameSync replaces a link at
  // bundle.html instead of writing through it.
  rmSync(tmpPath, { force: true });
  writeFileSync(tmpPath, finalHtml, { encoding: "utf-8", flag: "wx" });
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
