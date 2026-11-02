"use client";

/**
 * ccBridge — the Custom Apps capability bridge (Phase 2a: user / storage / ai
 * / tools).
 *
 * Two halves that speak one tiny RPC protocol over postMessage:
 *
 *  1. buildAppSrcDoc()  — injects a dependency-free `window.cc` SDK script into
 *     an app bundle before it is handed to <SandboxedHtml>. Inside the frame,
 *     every cc.* method posts `{ __ccsdk: true, id, method, args }` to the
 *     parent and awaits the matching `{ __ccsdk: true, id, result | error }`
 *     reply (30 s timeout). This holds even for `cc.tools.call()`, whose
 *     handshake with the backend can take two round-trips (see below) — the
 *     frame still only ever sends one request and awaits one reply.
 *  2. useCcBridge()     — the parent-side listener that validates each request,
 *     executes it against the App Runtime API (/api/apps/{slug}/…) with the
 *     VIEWER's session, and posts the reply back to the frame.
 *
 * The `__ccsdk` namespace flag is deliberately distinct from SandboxedHtml's
 * internal `__cc` flag (ccAction/ccSubmit/height) so the two protocols coexist
 * on the same window without ever colliding: each handler ignores the other's
 * messages.
 *
 * Besides the RPC calls, the SDK also mirrors frame errors (window.onerror,
 * unhandledrejection, console.error) to the parent as fire-and-forget
 * `{ __ccsdk, event: "console", … }` notifications — no `id`, so the RPC
 * broker ignores them; `useCcBridge({ onConsoleEvent })` surfaces them to
 * the Workshop's console drawer.
 *
 * `cc.tools.call(tool, args)` is the one RPC that can't be a simple
 * fetch-and-reply, because a human decision happens in between: dispatch()
 * POSTs `/api/apps/{slug}/tools/{tool}`; a 409 `confirmation_required` means
 * nothing ran yet, so it awaits `useCcBridge({ onToolConfirm })` for the
 * viewer's approve/deny before re-POSTing with `confirm:true`. All of that
 * multi-step handling happens on the PARENT side — invisible to the frame,
 * which just sees its one `call()` promise eventually resolve or reject.
 *
 * Trust model: the frame holds no token, no cookie, no credential — every call
 * is brokered by this page and executed as the viewing user (see RFC §4.4).
 *
 * Phase 2b adds `window.__ccTest` to the same injected script — a SEPARATE
 * global from `window.cc`, reusing the identical `__ccsdk` envelope with a
 * `test.` method prefix. It's a remote-control channel for `testRunner.ts`
 * (an outside, self-contained caller — NOT `useCcBridge`) to drive an app's
 * own DOM from a hidden offscreen iframe; see RFC §4.9. Apps never call it.
 */

import { useEffect, useRef } from "react";

// ─── In-frame SDK ─────────────────────────────────────────────────────────

/** Escape a value for safe embedding inside a <script> string literal. */
function safeScriptJson(value: unknown): string {
  return JSON.stringify(value ?? null)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

/**
 * The `window.cc` SDK injected into every app frame (preview AND published,
 * so behaviour is identical in both — no "works in preview, breaks live").
 * Dependency-free, promise-based, ~80 lines. `__SLUG__` / `__MODE__` are
 * replaced with JSON literals at build time.
 */
const CC_SDK = `
<script>
  (function () {
    var SLUG = __SLUG__;
    var MODE = __MODE__;
    var TIMEOUT_MS = 30000;
    var seq = 0;
    var pending = {};
    // Every cc.* method funnels here: post the request to the parent page and
    // await the matching reply by id. The parent brokers the actual API call.
    function call(method, args) {
      return new Promise(function (resolve, reject) {
        var id = "cc-" + MODE + "-" + (++seq) + "-" + Date.now();
        var timer = setTimeout(function () {
          delete pending[id];
          reject(new Error("cc." + method + " timed out after 30s"));
        }, TIMEOUT_MS);
        pending[id] = { resolve: resolve, reject: reject, timer: timer };
        try {
          parent.postMessage(
            { __ccsdk: true, id: id, method: method, args: args || [], slug: SLUG, mode: MODE },
            "*"
          );
        } catch (e) {
          clearTimeout(timer);
          delete pending[id];
          reject(e);
        }
      });
    }
    window.addEventListener("message", function (ev) {
      var d = ev.data;
      if (!d || typeof d !== "object" || d.__ccsdk !== true || !d.id) return;
      // A "test." method is an incoming REQUEST from an outside test runner
      // (see __ccTest below), not a reply to one of our own pending cc.*
      // calls — branch it off before the pending-lookup below, which only
      // ever matches replies keyed by ids *we* generated.
      if (typeof d.method === "string" && d.method.slice(0, 5) === "test.") {
        handleTestRequest(d);
        return;
      }
      var p = pending[d.id];
      if (!p) return;
      delete pending[d.id];
      clearTimeout(p.timer);
      if (d.error != null) p.reject(new Error(String(d.error)));
      else p.resolve(d.result);
    });
    // ── Console capture ──────────────────────────────────────────────
    // window.onerror / unhandledrejection / console.error are mirrored to
    // the parent as fire-and-forget notifications (event field, NO id — the
    // parent's RPC broker ignores them; its console listener picks them up).
    var CONSOLE_CAP = 2000;
    var lastConsoleKey = null;
    function postConsole(level, message, stack) {
      try {
        var msg = String(message == null ? "" : message).slice(0, CONSOLE_CAP);
        var stk = stack == null ? null : String(stack).slice(0, CONSOLE_CAP);
        // Dedupe identical consecutive messages (error loops spam otherwise).
        var key = level + "\\u0000" + msg + "\\u0000" + (stk || "");
        if (key === lastConsoleKey) return;
        lastConsoleKey = key;
        var payload = {
          __ccsdk: true,
          event: "console",
          level: level,
          message: msg,
          slug: SLUG,
          mode: MODE
        };
        if (stk) payload.stack = stk;
        parent.postMessage(payload, "*");
      } catch (e) {
        /* fire-and-forget */
      }
    }
    var prevOnError = window.onerror;
    window.onerror = function (message, source, lineno, colno, error) {
      postConsole("error", message, error && error.stack);
      if (typeof prevOnError === "function") {
        return prevOnError.apply(this, arguments);
      }
      return false;
    };
    window.addEventListener("unhandledrejection", function (ev) {
      var r = ev && ev.reason;
      if (r instanceof Error) {
        postConsole("error", "Unhandled rejection: " + r.message, r.stack);
      } else {
        var text;
        try { text = typeof r === "string" ? r : JSON.stringify(r); }
        catch (e) { text = String(r); }
        postConsole("error", "Unhandled rejection: " + text, null);
      }
    });
    var origConsoleError = console.error;
    console.error = function () {
      try { origConsoleError.apply(console, arguments); } catch (e) {}
      var parts = [];
      var stack = null;
      for (var i = 0; i < arguments.length; i++) {
        var a = arguments[i];
        if (a instanceof Error) {
          parts.push(a.message);
          if (!stack && a.stack) stack = a.stack;
        } else if (typeof a === "object" && a !== null) {
          try { parts.push(JSON.stringify(a)); }
          catch (e) { parts.push(String(a)); }
        } else {
          parts.push(String(a));
        }
      }
      postConsole("error", parts.join(" "), stack);
    };
    function table(name) {
      return {
        list: function (opts) { return call("storage.list", [name, opts || {}]); },
        get: function (key) { return call("storage.get", [name, key]); },
        set: function (key, value, opts) { return call("storage.set", [name, key, value, opts || {}]); },
        "delete": function (key) { return call("storage.delete", [name, key]); }
      };
    }
    window.cc = {
      user: function () { return call("user.me", []); },
      storage: { table: table, kv: table("kv") },
      ai: {
        complete: function (prompt, opts) { return call("ai.complete", [prompt, opts || {}]); }
      },
      tools: {
        // One request, one eventual reply, same as every other cc.* method —
        // the confirm handshake (409 -> human decision -> re-POST) all happens
        // on the PARENT side (useCcBridge's dispatch()); the frame just awaits
        // the final outcome. A truthy "queued" on the resolved value means
        // "sent to the Approvals inbox, not executed yet" - not a normal
        // result - apps should branch on it rather than treat it as done.
        call: function (tool, args) { return call("tools.call", [tool, args || {}]); }
      }
    };
    // ── __ccTest: remote-control channel for the test runner (Phase 2b) ──
    // A SEPARATE global from window.cc — apps must never call this
    // themselves, it is testing infrastructure, not app capability. It
    // grants ZERO new power: the app's own script already has full
    // run-of-the-DOM under sandbox="allow-scripts" (no allow-same-origin
    // needed for that). The only thing missing, and the only thing this
    // adds, is a way for code OUTSIDE the frame (testRunner.ts, driving a
    // hidden offscreen iframe) to reach in — the opaque origin means it
    // cannot touch this window's properties directly, so the "reach in"
    // still has to happen over the same postMessage RPC as cc.* (see the
    // "test." branch in the message listener above); window.__ccTest below
    // is what that branch calls into.
    function requireEl(selector) {
      var el = document.querySelector(selector);
      if (!el) throw new Error("element not found: " + selector);
      return el;
    }
    window.__ccTest = {
      click: function (selector) {
        requireEl(selector).click();
        return true;
      },
      type: function (selector, text) {
        var el = requireEl(selector);
        el.value = text;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      },
      select: function (selector, value) {
        var el = requireEl(selector);
        el.value = value;
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      },
      text: function (selector) {
        var el = document.querySelector(selector);
        return el ? (el.textContent == null ? "" : el.textContent) : null;
      },
      exists: function (selector) {
        return !!document.querySelector(selector);
      }
    };
    // Runs a test.* request against window.__ccTest and replies with the
    // same {__ccsdk,id,result|error} shape as every other RPC reply — the
    // test runner's own listener (its hidden-iframe counterpart to this
    // file's parent-side broker) awaits it exactly like useCcBridge's
    // caller awaits a cc.* reply.
    function handleTestRequest(d) {
      try {
        var fn = window.__ccTest[d.method.slice(5)];
        if (typeof fn !== "function") {
          throw new Error("unsupported in test mode: " + d.method);
        }
        var result = fn.apply(null, d.args || []);
        parent.postMessage({ __ccsdk: true, id: d.id, result: result }, "*");
      } catch (e) {
        parent.postMessage(
          { __ccsdk: true, id: d.id, error: e && e.message ? e.message : String(e) },
          "*"
        );
      }
    }
  })();
</script>`;

export interface BuildAppSrcDocOpts {
  slug: string;
  mode: "draft" | "live";
}

/**
 * Prepend the `window.cc` SDK to an app bundle so it is defined before any
 * app code runs. Injected right after <head> when present, else prepended.
 * The result is fed to <SandboxedHtml> (which supplies the sandbox + CSP).
 */
export function buildAppSrcDoc(
  bundleHtml: string,
  opts: BuildAppSrcDocOpts
): string {
  const sdk = CC_SDK.replace("__SLUG__", safeScriptJson(opts.slug)).replace(
    "__MODE__",
    safeScriptJson(opts.mode)
  );
  // (\s[^>]*)? — match <head> / <head lang…> but never <header>.
  const headMatch = /<head(\s[^>]*)?>/i.exec(bundleHtml);
  if (headMatch) {
    const at = headMatch.index + headMatch[0].length;
    return bundleHtml.slice(0, at) + sdk + bundleHtml.slice(at);
  }
  return sdk + bundleHtml;
}

// SandboxedHtml's `icons` prop pre-resolves Lucide icons into inline SVG
// (the sandboxed frame has no network access to fetch them itself) — but it
// needs to be TOLD which names an app's HTML actually references. This scans
// for both call shapes instructions.md documents for T1 apps: `ccIcon('Name')`
// and `<span data-cc-icon="Name">`. T2 apps import lucide-react directly and
// never need this — icons compile into the bundle like any other component.
const CC_ICON_CALL_RE = /ccIcon\(\s*["']([A-Za-z][A-Za-z0-9]*)["']\s*\)/g;
const CC_ICON_ATTR_RE = /data-cc-icon=["']([A-Za-z][A-Za-z0-9]*)["']/g;

/** Every distinct icon name an app's HTML asks for via `ccIcon(...)` or
 * `data-cc-icon="..."` — feed the result to `buildIconMap` (lib/iconSvg)
 * and pass it as `<SandboxedHtml icons={...}>`. */
export function extractCcIconNames(html: string): string[] {
  const names = new Set<string>();
  for (const m of html.matchAll(CC_ICON_CALL_RE)) names.add(m[1]);
  for (const m of html.matchAll(CC_ICON_ATTR_RE)) names.add(m[1]);
  return [...names];
}

// ─── Parent-side broker ───────────────────────────────────────────────────

interface CcSdkRequest {
  __ccsdk: true;
  id: string;
  method: string;
  args: unknown[];
  slug?: string;
  mode?: string;
}

function isCcSdkRequest(data: unknown): data is CcSdkRequest {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  return (
    d.__ccsdk === true &&
    typeof d.id === "string" &&
    typeof d.method === "string" &&
    Array.isArray(d.args)
  );
}

/** A console notification surfaced to the page (Workshop console drawer). */
export interface CcConsoleEvent {
  level: string;
  message: string;
  stack?: string;
}

/** Fire-and-forget console notification from the frame (event, NO id). */
interface CcConsoleMessage extends CcConsoleEvent {
  __ccsdk: true;
  event: "console";
  slug?: string;
  mode?: string;
}

function isCcConsoleMessage(data: unknown): data is CcConsoleMessage {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  return (
    d.__ccsdk === true &&
    d.event === "console" &&
    typeof d.level === "string" &&
    typeof d.message === "string" &&
    (d.stack === undefined || typeof d.stack === "string")
  );
}

/**
 * A `cc.tools.call()` the app made that hit a destructive tool with no
 * remembered grant (backend 409 `confirmation_required`) — `args` is the
 * server-echoed, constraint-merged version, not necessarily what the app
 * originally sent.
 */
export interface CcToolConfirmRequest {
  tool: string;
  args: Record<string, unknown>;
}

/** The viewer's decision on a CcToolConfirmRequest. */
export interface CcToolConfirmDecision {
  approved: boolean;
  /** Persist this as a standing "always allow" grant for this app + tool. */
  remember: boolean;
}

/** Read an app-supplied path segment (table / key), rejecting non-strings. */
function seg(v: unknown, what: string): string {
  if (typeof v !== "string" || v.length === 0 || v.length > 200) {
    throw new Error(`invalid ${what}`);
  }
  return encodeURIComponent(v);
}

/**
 * The words of a refused App Runtime call, for the app to show.
 *
 * `error` first: a 429 answers `{error: "ai_budget_exhausted"}`. Then FastAPI's
 * `detail`, a string such as the 502 of an AI answer with no text (task
 * 1128). Without it the app saw only "HTTP 502". The status is the last resort.
 */
export function refusalText(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const { error, detail } = body as { error?: unknown; detail?: unknown };
    if (error !== undefined && error !== null) return String(error);
    if (typeof detail === "string" && detail) return detail;
  }
  return `HTTP ${status}`;
}

async function jsonOrThrow(res: Response): Promise<unknown> {
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(refusalText(body, res.status));
  }
  return body;
}

/** Parse a JSON body, defaulting to `{}` on empty/invalid responses. */
async function readJsonBody(res: Response): Promise<Record<string, unknown>> {
  const body: unknown = await res.json().catch(() => null);
  return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
}

/** 200 → `body.result`; any other status → throw `body.error` (or the status). */
async function toolResultOrThrow(res: Response): Promise<unknown> {
  const body = await readJsonBody(res);
  if (!res.ok) {
    throw new Error(typeof body.error === "string" ? body.error : `HTTP ${res.status}`);
  }
  return body.result ?? null;
}

/** Execute one SDK request against the App Runtime API (viewer's session). */
async function dispatch(
  slug: string,
  msg: CcSdkRequest,
  onToolConfirm?: (req: CcToolConfirmRequest) => Promise<CcToolConfirmDecision>
): Promise<unknown> {
  const base = `/api/apps/${encodeURIComponent(slug)}`;
  const [a0, a1, a2, a3] = msg.args;
  switch (msg.method) {
    case "user.me": {
      return jsonOrThrow(await fetch(`${base}/me`));
    }
    case "storage.list": {
      const table = seg(a0, "table");
      const opts = (a1 ?? {}) as { scope?: string };
      const qs = opts.scope === "user" ? "?scope=user" : "";
      const body = (await jsonOrThrow(
        await fetch(`${base}/data/${table}${qs}`)
      )) as { rows?: unknown[] };
      return body?.rows ?? body;
    }
    case "storage.get": {
      const table = seg(a0, "table");
      const key = seg(a1, "key");
      const res = await fetch(`${base}/data/${table}/${key}`);
      if (res.status === 404) return null;
      const row = await jsonOrThrow(res);
      // Row shape is {key, value, …} — resolve with the stored value.
      if (row && typeof row === "object" && "value" in row) {
        return (row as { value: unknown }).value;
      }
      return row;
    }
    case "storage.set": {
      const table = seg(a0, "table");
      const key = seg(a1, "key");
      const opts = (a3 ?? {}) as { scope?: string };
      await jsonOrThrow(
        await fetch(`${base}/data/${table}/${key}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            value: a2,
            ...(opts.scope ? { scope: opts.scope } : {}),
          }),
        })
      );
      return true;
    }
    case "storage.delete": {
      const table = seg(a0, "table");
      const key = seg(a1, "key");
      const res = await fetch(`${base}/data/${table}/${key}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
      return true;
    }
    case "ai.complete": {
      const opts = (a1 ?? {}) as Record<string, unknown>;
      const payload =
        Array.isArray(a0) ? { messages: a0, ...opts } : { prompt: a0, ...opts };
      // 429 {error:"ai_budget_exhausted"} surfaces as a rejection the app can
      // catch and render.
      return jsonOrThrow(
        await fetch(`${base}/ai/complete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      );
    }
    case "tools.call": {
      if (typeof a0 !== "string" || a0.length === 0 || a0.length > 200) {
        throw new Error("invalid tool");
      }
      const tool = a0;
      const args = (a1 ?? {}) as Record<string, unknown>;
      const url = `${base}/tools/${encodeURIComponent(tool)}`;
      const post = (payload: Record<string, unknown>) =>
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

      const first = await post({ args, confirm: false });
      if (first.status === 202) {
        // ACTION_BROKER_ENFORCE is on for this tool: sent to the Approvals
        // inbox, nothing executed yet. The app sees a truthy `queued` and
        // should treat that as "pending approval", not a normal result.
        const body = await readJsonBody(first);
        return {
          queued: true,
          action_id: typeof body.action_id === "string" ? body.action_id : null,
        };
      }
      if (first.status !== 409) {
        return toolResultOrThrow(first);
      }
      // 409 confirmation_required — first attempt at a destructive tool with
      // no remembered grant; nothing executed yet. A human decision happens
      // here, brokered by whatever page installed onToolConfirm (e.g. the
      // run page's bottom-right toast), before we ever re-POST.
      const body = await readJsonBody(first);
      if (body.error !== "confirmation_required") {
        throw new Error(typeof body.error === "string" ? body.error : `HTTP ${first.status}`);
      }
      if (!onToolConfirm) {
        throw new Error("No confirmation UI installed for this app");
      }
      // Server-echoed args are the constraint-merged, source-of-truth
      // version of what will actually run — use those, not the original
      // request args, for both the confirm prompt and the re-POST.
      const confirmArgs = (body.args as Record<string, unknown> | undefined) ?? args;
      const decision = await onToolConfirm({ tool, args: confirmArgs });
      if (!decision.approved) throw new Error("Denied");
      const second = await post({
        args: confirmArgs,
        confirm: true,
        remember: decision.remember,
      });
      return toolResultOrThrow(second);
    }
    default:
      throw new Error(`unknown method: ${msg.method}`);
  }
}

/**
 * Install the parent-side `window.cc` broker for an app frame on this page.
 * Listens for `__ccsdk` requests, executes them against /api/apps/{slug}/…,
 * and posts the reply back to the requesting frame. Cleans up on unmount.
 */
export function useCcBridge(
  slug: string,
  opts?: {
    mode?: "draft" | "live";
    /** Invoked for `event: "console"` notifications from the app frame. */
    onConsoleEvent?: (e: CcConsoleEvent) => void;
    /**
     * Invoked when a `cc.tools.call()` hits a destructive tool with no
     * remembered grant (backend 409 `confirmation_required`). Resolve with
     * the viewer's decision; ccBridge re-POSTs with `confirm:true` when
     * approved, or rejects the app's call with `Error("Denied")` otherwise.
     * Apps calling `cc.tools.call` when no callback is installed get a
     * clear `Error("No confirmation UI installed for this app")` instead of
     * hanging or silently no-oping.
     */
    onToolConfirm?: (req: CcToolConfirmRequest) => Promise<CcToolConfirmDecision>;
  }
): void {
  const mode = opts?.mode ?? "live";
  // Ref'd so a new callback identity never tears down the listener.
  const onConsoleEventRef = useRef(opts?.onConsoleEvent);
  const onToolConfirmRef = useRef(opts?.onToolConfirm);
  useEffect(() => {
    onConsoleEventRef.current = opts?.onConsoleEvent;
    onToolConfirmRef.current = opts?.onToolConfirm;
  });
  useEffect(() => {
    let disposed = false;

    function onMessage(ev: MessageEvent) {
      const data: unknown = ev.data;
      // Console notifications (no id) are not RPC — surface and stop.
      if (isCcConsoleMessage(data)) {
        if (data.slug !== slug) return;
        onConsoleEventRef.current?.({
          level: data.level,
          message: data.message,
          ...(data.stack !== undefined ? { stack: data.stack } : {}),
        });
        return;
      }
      if (!isCcSdkRequest(data)) return;
      // Ignore requests stamped for a different app (two frames on one page).
      if (data.slug && data.slug !== slug) return;
      const source = ev.source;
      if (!source) return;

      // targetOrigin "*" is acceptable (and required) here: the app frame is a
      // sandboxed srcDoc WITHOUT allow-same-origin, so it has an opaque origin
      // ("null") that cannot be named as a targetOrigin. The reply still goes
      // only to that specific window (ev.source), which we captured above.
      const reply = (payload: { result?: unknown; error?: string }) => {
        try {
          (source as Window).postMessage(
            { __ccsdk: true, id: data.id, ...payload },
            "*"
          );
        } catch {
          // Frame may have been torn down — nothing to do.
        }
      };

      dispatch(slug, data, onToolConfirmRef.current)
        .then((result) => {
          if (!disposed) reply({ result: result ?? null });
        })
        .catch((err: unknown) => {
          if (!disposed)
            reply({ error: err instanceof Error ? err.message : String(err) });
        });
    }

    window.addEventListener("message", onMessage);
    return () => {
      disposed = true;
      window.removeEventListener("message", onMessage);
    };
  }, [slug, mode]);
}
