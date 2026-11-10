/**
 * PKG-8a fix round 2 fence (R7): the shell modules that every page loads import no component.
 *
 * `packageManifest.ts` runs in the sidebar, the phone drawer and the command bar of every page,
 * also for an org with the package host off. `liveRefresh.ts` runs under it. Fix round 1 made
 * them import `PROJECTS_CHANGED_EVENT` from `components/projects/ProjectToolCards.tsx`. That file
 * pulled the chat renderer (`genUITemplates`, `MarkdownMessage`, `react-markdown`,
 * `react-syntax-highlighter`) into the shell, past the `dynamic()` load of the dock.
 *
 * The rule: a value import of these modules, followed through every module it reaches in
 * `src/`, never reaches `src/components/` and never names a chat renderer package. A type-only
 * import is erased at build, so it does not count.
 *
 * Mutation: import `PROJECTS_CHANGED_EVENT` from `@/components/projects/ProjectToolCards` in
 * `liveRefresh.ts` again. Both tests turn red.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const ENTRIES = ["lib/shell/packageManifest.ts", "lib/liveRefresh.ts", "lib/shell/manifestCache.ts"];
const HEAVY = /^(react-markdown|react-syntax-highlighter|remark-|rehype-)/;

const rel = (abs: string): string => relative(SRC, abs).replace(/\\/g, "/");

/** The value imports of one file: `import x from "y"` and `export ... from "y"`, never `import type`. */
function valueImports(abs: string): string[] {
  const src = readFileSync(abs, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const out: string[] = [];
  const re = /^\s*(import|export)\s+(type\s+)?([\s\S]*?)\s+from\s+["']([^"']+)["']/gm;
  for (const m of src.matchAll(re)) {
    if (m[2]) continue;
    const names = m[3].trim();
    // `import { type A, type B } from` brings only types too.
    if (/^\{[\s\S]*\}$/.test(names) && names.slice(1, -1).split(",").every((n) => !n.trim() || /^type\s/.test(n.trim()))) continue;
    out.push(m[4]);
  }
  for (const m of src.matchAll(/^\s*import\s+["']([^"']+)["']/gm)) out.push(m[1]);
  return out;
}

/** The file a specifier names inside `src/`, or null for a package. */
function resolveSpec(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = join(SRC, spec.slice(2));
  else if (spec.startsWith(".")) base = resolve(dirname(from), spec);
  else return null;
  for (const ext of ["", ".ts", ".tsx", "/index.ts", "/index.tsx"]) {
    if (existsSync(base + ext) && !base.endsWith("/") && (ext || /\.(ts|tsx)$/.test(base))) return base + ext;
  }
  return null;
}

/** Each file of `src/` that a value import of `entry` reaches, and each package it names. */
function closure(entry: string): { files: Set<string>; packages: Set<string> } {
  const files = new Set<string>();
  const packages = new Set<string>();
  const todo = [join(SRC, entry)];
  while (todo.length) {
    const file = todo.pop()!;
    if (files.has(rel(file))) continue;
    files.add(rel(file));
    for (const spec of valueImports(file)) {
      const next = resolveSpec(file, spec);
      if (next) todo.push(next);
      else if (!spec.startsWith("@/") && !spec.startsWith(".")) packages.add(spec);
    }
  }
  return { files, packages };
}

describe("the shell modules of every page stay light (PKG-8a fix round 2)", () => {
  it("import nothing from @/components/ directly", () => {
    for (const entry of ENTRIES) {
      const direct = valueImports(join(SRC, entry)).filter((s) => s.startsWith("@/components/") || /(^|\/)components\//.test(s));
      expect(direct, entry).toEqual([]);
    }
  });

  it("reach no component and no chat renderer through any chain", () => {
    for (const entry of ENTRIES) {
      const { files, packages } = closure(entry);
      expect([...files].filter((f) => f.startsWith("components/")), entry).toEqual([]);
      expect([...packages].filter((p) => HEAVY.test(p)), entry).toEqual([]);
    }
  });
});
