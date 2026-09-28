/**
 * A JSON body to the gateway says it is JSON (WS-27bm S15).
 *
 * Spec: project-docs/specs/projects_ai_chat.md §21.
 *
 * For a string body, Node's `fetch` sends `text/plain;charset=UTF-8`. FastAPI
 * then does not parse the body, and an endpoint that takes a list answers 422.
 * Commit e92d0620 dropped the JSON header from nine routes. From then on every
 * chat save on production got 422, and both clients hid the error.
 *
 * `gatewayFetch` now supplies the header for a string body (rule 10), and
 * `gatewayFetch.test.ts` proves that. This sweep is the second fence. It reads
 * every `app/api/**\/route.ts` and fails when a call sends
 * `body: JSON.stringify(...)` and names no JSON content type. So a route stays
 * correct even if a call moves off the seam.
 *
 * A call passes when its own text names `content-type`, or when its `headers:`
 * value is a name that this file defines with `"Content-Type": "application/json"`.
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const API = fileURLToPath(new URL("../app/api", import.meta.url));

function routeFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...routeFiles(p));
    else if (name === "route.ts") out.push(p);
  }
  return out;
}

/** Each `fetch(` or `gatewayFetch(` call in `src`, with its full argument text. */
function calls(src: string): Array<{ line: number; text: string }> {
  const out: Array<{ line: number; text: string }> = [];
  const re = /\b(?:gatewayFetch|fetch)\s*\(/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    let i = m.index + m[0].length;
    let depth = 1;
    while (i < src.length && depth > 0) {
      if (src[i] === "(") depth += 1;
      else if (src[i] === ")") depth -= 1;
      i += 1;
    }
    out.push({ line: src.slice(0, m.index).split("\n").length, text: src.slice(m.index, i) });
  }
  return out;
}

const JSON_TYPE = /["']Content-Type["']\s*:\s*["']application\/json/i;

/** True when the name in `headers: <name>` is built with a JSON content type in this file. */
function headersNameIsJson(src: string, call: string): boolean {
  // `headers: name`, `headers: await name()`, or the shorthand `headers,`.
  const HEADERS_ARG = /\bheaders\s*(?::\s*(?:await\s+)?([A-Za-z_$][\w$]*)|[,}])/;
  const m = HEADERS_ARG.exec(call);
  if (!m) return false;
  const name = m[1] ?? "headers";
  if (name === "gatewayHeaders") return false;
  const def = new RegExp(`(?:const|let|function)\\s+${name}\\b[\\s\\S]{0,300}`).exec(src);
  return !!def && JSON_TYPE.test(def[0]);
}

export function offenders(): string[] {
  const bad: string[] = [];
  for (const file of routeFiles(API)) {
    const src = readFileSync(file, "utf8");
    for (const { line, text } of calls(src)) {
      if (!/\bbody\s*:\s*JSON\.stringify\s*\(/.test(text)) continue;
      if (/content-type/i.test(text)) continue;
      if (headersNameIsJson(src, text)) continue;
      bad.push(`${relative(API, file).replace(/\\/g, "/")}:${line}`);
    }
  }
  return bad;
}

describe("a JSON body to the gateway names its content type", () => {
  it("finds the route files at all", () => {
    // A sweep that reads nothing passes everything.
    expect(routeFiles(API).length).toBeGreaterThan(50);
  });

  it("no route sends body: JSON.stringify(...) without a JSON content type", () => {
    expect(offenders()).toEqual([]);
  });

  it("the nine routes of e92d0620 set the header themselves", () => {
    const nine = [
      "chat/sessions/route.ts",
      "chat/sessions/[sessionId]/messages/route.ts",
      "chat/sessions/[sessionId]/agents/route.ts",
      "chat/sessions/[sessionId]/participants/route.ts",
      "chat/sessions/[sessionId]/participants/[subject]/route.ts",
      "chat/sessions/[sessionId]/presence/route.ts",
      "chat/sessions/[sessionId]/room/route.ts",
      "observability/avatars/generate/route.ts",
      "observability/avatars/[name]/route.ts",
    ];
    for (const rel of nine) {
      const src = readFileSync(join(API, rel), "utf8");
      const sends = calls(src).filter((c) => /\bbody\s*:/.test(c.text));
      expect(sends.length, rel).toBeGreaterThan(0);
      for (const c of sends) expect(JSON_TYPE.test(c.text), `${rel}:${c.line}`).toBe(true);
    }
  });
});
