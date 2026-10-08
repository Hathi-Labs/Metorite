/**
 * The chat's failed-turn card (owner report, 2026-10-08).
 *
 * The member saw a raw Python repr whose class name ran past the box, and an
 * operator command. These tests hold the four rules that replaced it. Each
 * names the mutation it was run against.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ErrorCardView } from "@/components/ChatErrorCard";
import { canRetry, retryPlan } from "@/lib/chatRetry";
import {
  ChatRunError,
  RUN_ERROR_WORDS,
  codeForStatus,
  parseStoredRunError,
  runErrorView,
  type RunErrorCode,
  type RunErrorView,
} from "@/lib/runErrors";

const REPR =
  "(\"<class 'agent_framework_openai._chat_completion_client.OpenAIChatCompletionClient'> " +
  "service failed to complete the prompt: Connection error.\", APIConnectionError('Connection error.'))";

const SRC = fileURLToPath(new URL("..", import.meta.url));

const view = (code: RunErrorCode, ref: string | null = "3f9c2a1b"): RunErrorView => ({ code, ref, raw: REPR });

const render = (v: RunErrorView, opts: { open?: boolean; retry?: boolean } = {}) =>
  renderToStaticMarkup(
    createElement(ErrorCardView, {
      error: v,
      defaultOpen: opts.open ?? false,
      onRetry: opts.retry === false ? undefined : () => {},
    }),
  );

/** The card's text with the fold cut out. */
const outsideFold = (html: string) => html.replace(/<div[^>]*data-chat-error-fold[\s\S]*$/, "");

describe("the code maps to the product's words", () => {
  // Mutation: point `connection` at the `unknown` words, and this fails.
  it("connection says what the owner asked for", () => {
    expect(RUN_ERROR_WORDS.connection.body).toBe(
      "Metorite lost its connection while answering. This usually happens when the app updates. " +
        "Your message is saved. Press Retry.",
    );
    expect(RUN_ERROR_WORDS.credits.body).toContain("Your organization is out of AI credits");
  });

  it.each(Object.keys(RUN_ERROR_WORDS) as RunErrorCode[])("%s draws its own words", (code) => {
    const html = render(view(code));
    expect(html).toContain(RUN_ERROR_WORDS[code].title);
    expect(html).toContain(RUN_ERROR_WORDS[code].body.slice(0, 30));
  });

  it("no word names a class, a stack or a shell command", () => {
    for (const w of Object.values(RUN_ERROR_WORDS)) {
      const text = `${w.title} ${w.body}`;
      expect(text).not.toMatch(/Error\b|<class|Traceback|journalctl|sudo|`/);
    }
  });

  // Mutation: return the frame's text as the code, and the off-list case fails.
  // Mutation: move the reference line out of the fold, and this fails.
  it("the reference line sits in the fold, and only when the server named a ref", () => {
    expect(render(view("unknown", null), { open: true })).not.toContain("Reference");
    const open = render(view("unknown"), { open: true });
    expect(open).toContain("Reference: <span class=\"font-mono\">3f9c2a1b</span>");
    expect(outsideFold(open)).not.toContain("Reference");
    expect(RUN_ERROR_WORDS.unknown.body).not.toContain("below");
  });

  it("an unknown or missing code draws the unknown words with the ref", () => {
    expect(runErrorView({ raw: REPR, code: "APIConnectionError" }).code).toBe("unknown");
    expect(runErrorView({ raw: REPR }).code).toBe("unknown");
    expect(render(view("unknown"))).toContain("Something went wrong");
    expect(render(view("unknown"), { open: true })).toContain("3f9c2a1b");
  });

  it("the stream error carries the server's code and ref", () => {
    const e = new ChatRunError(REPR, "connection", "3f9c2a1b");
    expect(runErrorView({ raw: e.message, code: e.code, ref: e.ref })).toEqual(view("connection"));
    expect(new ChatRunError("x", "Nonsense", 7).code).toBe("unknown");
  });

  // Mutation: drop the frame parse, and the raw keeps its `data:` prefix.
  it("a refused request reads the route's own error frame, then the status", () => {
    const body = `data: ${JSON.stringify({ type: "error", content: "Gateway unreachable: fetch failed", code: "connection" })}\n\n`;
    expect(runErrorView({ raw: body, status: 502 })).toEqual({
      code: "connection",
      ref: null,
      raw: "Gateway unreachable: fetch failed",
    });
    expect(runErrorView({ raw: "Payment required", status: 402 }).code).toBe("credits");
  });

  it("the status table matches classify_status in run_errors.py", () => {
    // 401 and 404 differ from the Python table on purpose (see codeForStatus).
    const table: Array<[number, RunErrorCode]> = [
      [402, "credits"], [403, "permission"], [429, "rate_limited"], [409, "run_in_progress"],
      [408, "timeout"], [504, "timeout"], [400, "model_refused"], [413, "model_refused"],
      [422, "model_refused"], [502, "connection"], [503, "connection"], [401, "signed_out"],
      [404, "unknown"], [500, "unknown"],
    ];
    for (const [status, code] of table) expect(codeForStatus(status)).toBe(code);
  });

  it("a stored payload that is malformed is unknown, never a crash", () => {
    expect(parseStoredRunError("{not json").code).toBe("unknown");
    expect(parseStoredRunError(JSON.stringify({ code: "credits", ref: "ab", raw: "r" }))).toEqual({
      code: "credits",
      ref: "ab",
      raw: "r",
    });
  });
});

describe("the raw text stays inside the fold", () => {
  // Mutation: print `error.raw` beside the body, and this fails.
  it("shut, the card shows no raw text at all", () => {
    const html = render(view("connection"));
    expect(html).not.toContain("APIConnectionError");
    expect(html).not.toContain("agent_framework_openai");
    expect(html).toContain("Show full error");
    expect(html).toContain("Copy error");
  });

  it("open, the raw text is in the fold and nowhere else", () => {
    const html = render(view("connection"), { open: true });
    expect(html).toContain("agent_framework_openai");
    expect(outsideFold(html)).not.toContain("agent_framework_openai");
  });

  // Mutation: remove `wrap-anywhere` from the <pre>, or `min-w-0` from the
  // card, and this fails. The visual rig measures scrollWidth for real.
  it("every text node can wrap a long token, and the raw block scrolls in place", () => {
    const html = render(view("connection"), { open: true });
    expect(html).toMatch(/data-chat-error-card[^>]*class="[^"]*\bmin-w-0\b[^"]*\boverflow-hidden\b/);
    for (const tag of html.match(/<(p|pre)\b[^>]*>/g) ?? []) {
      expect(tag).toMatch(/\bwrap-anywhere\b/);
      expect(tag).toMatch(/\bmin-w-0\b/);
    }
    expect(html).toMatch(/<pre[^>]*\boverflow-auto\b/);
  });
});

describe("no member ever sees operator text", () => {
  // Owner call on PR #733: the org admin flag marks the admin of EVERY
  // customer org, so the card shows no operator text to anyone. The card no
  // longer reads the access answer, so admin and member render the same.
  // Mutation: put a `sudo journalctl -u acb-gateway` line back in the fold,
  // and this fails.
  const OPERATOR = /journalctl|sudo|acb-/;
  it.each(Object.keys(RUN_ERROR_WORDS) as RunErrorCode[])("%s, shut and open", (code) => {
    for (const ref of ["3f9c2a1b", null]) {
      const v: RunErrorView = { code, ref, raw: "Connection error." };
      expect(render(v)).not.toMatch(OPERATOR);
      expect(render(v, { open: true })).not.toMatch(OPERATOR);
    }
  });

  it("the card takes no admin flag, so it cannot gate operator text on one", () => {
    const src = readFileSync(join(SRC, "components/ChatErrorCard.tsx"), "utf8");
    expect(src).not.toMatch(/useAccess|isAdmin|is_admin/);
  });
});

describe("Retry re-sends the member's last message", () => {
  const thread = [
    { id: "u1", role: "user" as const, content: "first question" },
    { id: "a1", role: "assistant" as const, content: "first answer" },
    { id: "u2", role: "user" as const, content: "Build a project from the email." },
    { id: "e1", role: "system" as const, content: `__ERROR__${JSON.stringify(view("connection"))}` },
  ];

  // Mutation: resend the FIRST user message, or keep the failed pair, and
  // this fails.
  it("from the error card: the last message, and the failed pair leaves", () => {
    expect(retryPlan(thread, "e1")).toEqual({ resend: "Build a project from the email.", drop: ["e1", "u2"] });
  });

  it("from an answer: the message before that answer (the old behaviour)", () => {
    expect(retryPlan(thread, "a1")).toEqual({ resend: "first question", drop: ["a1", "u1"] });
  });

  it("a user turn or a plain system note offers no retry", () => {
    expect(retryPlan(thread, "u2")).toBeNull();
    expect(canRetry({ role: "system", content: "[CONTEXT SUMMARY]" })).toBe(false);
  });

  // Mutation: drop `words.retry`, and the credits card offers a Retry.
  it("the card draws Retry where it can help, and not where it cannot", () => {
    expect(render(view("connection"))).toContain(">Retry<");
    expect(render(view("credits"))).not.toContain(">Retry<");
    expect(render(view("permission"))).not.toContain(">Retry<");
    expect(render(view("connection"), { retry: false })).not.toContain(">Retry<");
  });
});
