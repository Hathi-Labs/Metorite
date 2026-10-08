/**
 * The pin above the composer (spec `projects_ai_chat.md` §24 rule 1): an
 * element that needs the member stays in view until it is answered.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `pendingAsk` ignores the generative-UI ask -> "the owner's picker waits";
 * - an answered picker still pins -> "an answer clears the pin";
 * - a blocking picker pins after its run ended -> "a run that ended without
 *   an answer clears the pin";
 * - a non-blocking ask pins after the member wrote again -> "a later message
 *   answers a non-blocking ask";
 * - the pin reads a second queue -> "the confirmation queue is the one source";
 * - the bar is not a button, or draws while its element is in view -> the
 *   `AskPin` cases;
 * - `AgentChat` stops mounting the bar in the composer -> "the composer
 *   mounts the bar".
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AskPin from "@/components/AskPin";
import { HITL_TARGET, PIN_LEAD, pendingAsk, type AskSources } from "@/lib/askPin";
import { EMPTY_CONFIRMATIONS, confirmationReducer } from "@/lib/confirmationQueue";
import { OWNER_PICKER, ownerTurn } from "@/lib/ownerTurn.fixture";

function sources(over: Partial<AskSources> = {}): AskSources {
  return {
    confirmations: [],
    elicitation: null,
    userInput: null,
    messages: ownerTurn(),
    runActive: true,
    answered: new Set(),
    ...over,
  };
}

describe("what waits on the member", () => {
  it("the owner's picker waits while its run is live", () => {
    const ask = pendingAsk(sources());
    expect(ask).toMatchObject({
      kind: "choice",
      title: "Which tags should I register on Metorite?",
      target: "genui:req-tags-1",
    });
  });

  it("an answer clears the pin", () => {
    expect(pendingAsk(sources({ answered: new Set([OWNER_PICKER.request_id]) }))).toBeNull();
  });

  it("a run that ended without an answer clears the pin", () => {
    expect(pendingAsk(sources({ runActive: false }))).toBeNull();
  });

  it("a non-blocking ask waits until the member writes again", () => {
    const { request_id: _drop, ...loose } = OWNER_PICKER;
    void _drop;
    const [user, turn] = ownerTurn();
    const msgs = [user, { ...turn, customEvents: [{ name: "generative_ui", value: loose }] }];
    expect(pendingAsk(sources({ messages: msgs, runActive: false }))?.kind).toBe("choice");
    // A later message answers a non-blocking ask.
    const later = [...msgs, { id: "u2", role: "user", content: "Feature and UX", timestamp: 0 }];
    expect(pendingAsk(sources({ messages: later, runActive: false }))).toBeNull();
  });

  it("an answer card is not an ask", () => {
    const [user, turn] = ownerTurn();
    const card = { type: "template", props: { name: "statDashboard", data: { title: "Load" } } };
    const msgs = [user, { ...turn, customEvents: [{ name: "generative_ui", value: card }] }];
    expect(pendingAsk(sources({ messages: msgs }))).toBeNull();
  });

  it("the confirmation queue is the one source, with its 1 of N", () => {
    let q = EMPTY_CONFIRMATIONS;
    for (const id of ["r1", "r2", "r3"]) {
      q = confirmationReducer(q, { type: "requested", value: { request_id: id, title: `Add tag ${id}?` } });
    }
    const ask = pendingAsk(sources({ confirmations: q.cards }));
    expect(ask).toEqual({ kind: "confirm", title: "Add tag r1?", count: 3, target: HITL_TARGET });
    // Answering each card removes it from the queue, and the pin follows.
    for (const key of ["r1", "r2", "r3"]) q = confirmationReducer(q, { type: "answering", key });
    expect(pendingAsk(sources({ confirmations: q.cards, messages: [] }))).toBeNull();
  });

  it("a question card waits, and its answer clears it", () => {
    const elicitation = { questions: [{ header: "Scope", question: "Which project?" }] };
    expect(pendingAsk(sources({ elicitation, messages: [] }))).toMatchObject({ kind: "question", title: "Which project?" });
    expect(pendingAsk(sources({ elicitation: null, messages: [] }))).toBeNull();
  });
});

describe("the bar", () => {
  const ask = { kind: "choice" as const, title: "Which tags?", count: 1, target: "genui:x" };
  const draw = (a: typeof ask | null, inView = false) =>
    renderToStaticMarkup(createElement(AskPin, { ask: a, threadRef: createRef<HTMLElement>(), initialInView: inView }));

  it("is a button a keyboard reaches, with the element's words", () => {
    const html = draw(ask);
    expect(html).toMatch(/^<div[^>]*data-ask-pin/);
    expect(html).toContain("<button");
    expect(html).toContain(PIN_LEAD.choice);
    expect(html).toContain("Which tags?");
    expect(html).toContain('aria-label="Waiting for your choice: Which tags?. Show it"');
  });

  it("draws nothing when nothing waits, or while the element is in view", () => {
    expect(draw(null)).toBe("");
    expect(draw(ask, true)).toBe("");
  });

  it("names the queue's count", () => {
    expect(draw({ ...ask, kind: "confirm", count: 4 } as never)).toContain("1 of 4");
  });

  it("the composer mounts the bar", () => {
    const src = readFileSync(fileURLToPath(new URL("../components/AgentChat.tsx", import.meta.url)), "utf8");
    const form = src.slice(src.indexOf("<form onSubmit={handleSubmit}"));
    expect(form).toContain("<AskPin ask={waiting} threadRef={threadRef} />");
    expect(src).toContain(`data-chat-ask={HITL_TARGET}`);
  });
});
