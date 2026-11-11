#!/usr/bin/env node
// App Workshop · T2 (React) dependency installer — platform-owned, not
// authored by the builder agent (same "not editable by the LLM" property as
// build_t2.mjs). This is the ONLY sanctioned way a T2 app adds a package
// beyond the default vendored react/react-dom/@cc/ui: the builder agent runs
// this via its own native shell rather than a raw `npm install`, so this
// script is the enforcement point for what makes "install anything" safe
// here — install-time code execution and disk are the only new risks (an
// app's JS only ever runs client-side in the viewer's browser, unchanged;
// see docs/app-workshop/README.md §7 point 6):
//
//   1. `--ignore-scripts` — blocks the standard npm supply-chain RCE vector
//      (a malicious package's install/postinstall script running arbitrary
//      code on this host/sandbox before any of it ships anywhere). Same
//      rationale as packages/acb_skills/acb_skills/loader.py's wheel-only
//      installs for the gateway's own Python deps.
//   2. A hard cap on the resulting `node_modules` size — per-app installs
//      are otherwise unbounded, and VPS disk is not.
//   3. A package-spec allowlist regex — rejects anything that isn't a plain
//      "name" / "name@version" / "@scope/name@version" token, so a spec
//      can't smuggle in an npm flag (e.g. `--registry=...`) or a URL. Same
//      shape as acb_skills/dep_tools.py's `_SPEC_RE` for the Python side.
//
// Isolation between apps is structural, not something this script has to
// enforce: node_modules lands inside THIS app's own workspace directory,
// which is already the durability/build/publish unit for exactly one app —
// nothing here is shared with, or reachable from, any other app's workspace.
//   4. Task 2464: a dependency that npm takes from disk can do that. A
//      `file:` or `link:` spec, a path, a `workspaces` field, or a lock
//      entry that is a link makes npm link or copy another dir into
//      node_modules. So the app's package.json and lock file may name only
//      registry versions (`localDepRefusal`). build_t2.mjs refuses a link
//      that leads out of the app dir as well.
//
// Once a workspace has its own node_modules, build_t2.mjs switches from the
// shared vendor cache + import allowlist to standard npm resolution rooted
// at the workspace (see that file's `hasAppDeps` branch). This is opt-in per
// app — most T2 apps never call this and keep paying zero extra build/disk
// cost for it.
//
// Usage: node install_t2_deps.mjs <workspace-dir> <package-spec> [<package-spec> ...]
// Exit 0 + what was installed on stdout on success; exit 1 + reason on stderr.

import { existsSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

// Plain package name (optionally scoped) with an optional version/range —
// no flags, no URLs, no whitespace.
const SPEC_RE = /^(@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*(@[\w.^~*<>=|-]+)?$/;

// A generous but real ceiling — three.js + @react-three/fiber + @react-three/
// drei together run well under this; it exists to stop one app from silently
// eating a meaningful fraction of the VPS's disk, not to police normal use.
const MAX_NODE_MODULES_BYTES = 300 * 1024 * 1024;

// A registry version, range or tag: no ":" (file:, link:, git+, http:,
// npm:, workspace:), no "/" or "\" (a path, user/repo), no leading ".".
const REGISTRY_RANGE_RE = /^(?!\s*\.)[\w.^~*<>=|\s-]*$/;
const DEP_FIELDS = [
  "dependencies",
  "devDependencies",
  "optionalDependencies",
  "peerDependencies",
  "overrides",
];

function fail(message) {
  process.stderr.write(`install_t2_deps: ${message}\n`);
  process.exit(1);
}

function readJsonOrNull(path) {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return undefined; // present but unreadable
  }
}

/** Why npm may not install for this app, or null (task 2464). npm takes a
 * `file:` or `link:` dependency, a path, or a `workspaces` entry from disk,
 * and links or copies it into node_modules. Such a dir can be another app's. */
function localDepRefusal(workspace) {
  const pkg = readJsonOrNull(join(workspace, "package.json"));
  if (pkg === undefined) return "package.json is not valid JSON";
  if (pkg) {
    if (pkg.workspaces !== undefined) return "package.json has a workspaces field";
    for (const field of DEP_FIELDS) {
      for (const [name, spec] of Object.entries(pkg[field] || {})) {
        if (typeof spec !== "string" || !REGISTRY_RANGE_RE.test(spec)) {
          return `package.json ${field} names ${JSON.stringify(name)} as ${JSON.stringify(spec)}`;
        }
      }
    }
  }
  for (const lockName of ["package-lock.json", "npm-shrinkwrap.json"]) {
    const lock = readJsonOrNull(join(workspace, lockName));
    if (lock === undefined) return `${lockName} is not valid JSON`;
    for (const [key, entry] of Object.entries((lock && lock.packages) || {})) {
      if (key === "" || !entry || typeof entry !== "object") continue;
      const resolved = typeof entry.resolved === "string" ? entry.resolved : "";
      if (entry.link || (resolved && !resolved.startsWith("https://"))) {
        return `${lockName} takes ${JSON.stringify(key)} from disk`;
      }
    }
  }
  return null;
}

function dirSize(dir) {
  let total = 0;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return 0;
  }
  for (const entry of entries) {
    if (entry.isSymbolicLink()) continue; // don't follow — avoids cycles/escapes
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      total += dirSize(full);
    } else if (entry.isFile()) {
      try {
        total += statSync(full).size;
      } catch {
        // file vanished mid-walk — ignore
      }
    }
  }
  return total;
}

const workspace = resolve(process.argv[2] || "");
const specs = process.argv.slice(3);

if (!workspace || !existsSync(workspace)) {
  fail(`workspace not found: ${workspace || "(none given)"}`);
}
if (specs.length === 0) {
  fail("no package specs given — usage: install_t2_deps.mjs <workspace> <spec> [<spec> ...]");
}

const rejected = specs.filter((s) => !SPEC_RE.test(s));
if (rejected.length > 0) {
  fail(
    `rejected package spec(s): ${rejected.join(", ")} — plain names only ` +
      `("name", "name@version", or "@scope/name@version"), no flags, no URLs.`,
  );
}

const localDeps = localDepRefusal(workspace);
if (localDeps) {
  fail(
    `${localDeps}. An app can install only registry packages, by name and version. ` +
      `Remove the dependency that names a file, a link, a path or a URL, then install again.`,
  );
}

const pkgJsonPath = join(workspace, "package.json");
if (!existsSync(pkgJsonPath)) {
  writeFileSync(
    pkgJsonPath,
    JSON.stringify({ name: "t2-app", private: true, version: "0.0.0" }, null, 2) + "\n",
  );
}

const npmResult = spawnSync(
  "npm",
  ["install", "--ignore-scripts", "--no-audit", "--no-fund", "--save-exact", ...specs],
  { cwd: workspace, encoding: "utf-8", timeout: 180_000 },
);

if (npmResult.error) {
  fail(`failed to run npm: ${npmResult.error.message}`);
}
if (npmResult.status !== 0) {
  fail(`npm install failed:\n${(npmResult.stderr || npmResult.stdout || "").trim()}`);
}

const modulesDir = join(workspace, "node_modules");
const size = dirSize(modulesDir);
if (size > MAX_NODE_MODULES_BYTES) {
  rmSync(modulesDir, { recursive: true, force: true });
  fail(
    `node_modules too large after installing ${specs.join(", ")} ` +
      `(${(size / 1024 / 1024).toFixed(1)}MiB > ${MAX_NODE_MODULES_BYTES / 1024 / 1024}MiB cap) — ` +
      `removed. Try fewer/lighter packages.`,
  );
}

process.stdout.write(
  `install_t2_deps: ok, installed ${specs.join(", ")} ` +
    `(node_modules: ${(size / 1024 / 1024).toFixed(1)}MiB)\n`,
);
