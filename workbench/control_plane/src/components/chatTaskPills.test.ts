/**
 * Task numbers in the MAIN chat and in its cards (owner report, 2026-10-19).
 *
 * Screenshot 1: the main chat asked the Projects assistant, which made nine
 * tasks, and the answer listed `- #234 Spec tool-gap detection — …` to
 * `#242`. Not one drew as a pill. Screenshot 2: a "Link these tasks?" card
 * drew `From #247 …` and `To #248 …` as chips that did not open the task.
 *
 * The causes, one fence each:
 *
 * 1. The main chat runs the orchestrator, and `TASK_REF_AGENTS` left it out
 *    (it predates D99, the one assistant).
 * 2. A card's task pill never linked (`CardFieldValue`), and a `#5 «X»` in
 *    card text drew as quiet text (`FencedName`).
 * 3. A miss stayed in the cache for the session, so a number asked for before
 *    a write in the same turn stayed text after it (`revalidate`).
 * 4. Two spaces can each hold a #234. The title the answer wrote after the
 *    number now picks the one it names (`pickTask`'s `echoed`).
 *
 * vitest here is node-env with no DOM. `ControlLink` is a stub that keeps its
 * `onActivate`, as in `chatTaskRef.test.ts`.
 */
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const activations: Array<{ href: string; go: () => void }> = [];

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/ControlLink", () => ({
  ControlLink: ({
    href,
    onActivate,
    children,
    ...rest
  }: { href: string; onActivate: () => void; children?: ReactNode } & Record<string, unknown>) => {
    activations.push({ href, go: onActivate });
    return createElement("a", { href, ...rest }, children);
  },
}));

import ConfirmationCard from "@/components/ConfirmationCard";
import FencedText from "@/components/FencedText";
import MessageBubble from "@/components/MessageBubble";
import type { ChatMessage } from "@/hooks/useAgentChat";
import { agentAuthor } from "@/lib/projectsAgent";
import {
  createTaskRefResolver,
  pickTask,
  revalidateStep,
  taskRefResolver,
  type TaskRefHit,
} from "@/lib/taskRefs";

const SESSION = "chatTaskPills.test";

/** The nine tasks of screenshot 1. The first two titles are the owner's. */
const TITLES: Record<number, string> = {
  234: "Spec tool-gap detection",
  235: "Design the user-tool registry schema",
  236: "Build the sandbox runner",
  237: "Wire the runner to the registry",
  238: "Add the tool review card",
  239: "Write the tool audit log",
  240: "Expose user tools to the chat",
  241: "Test the sandbox limits",
  242: "Document the user-tool flow",
};
const idOf = (n: number) => `00000000-0000-4000-8000-000000000${n}`;
const NINE = Object.keys(TITLES).map(Number);

/** Screenshot 2's two tasks. */
const T247 = "Build the memory store and write path";
const T248 = "Build short-term session memory";

function row(n: number, title: string, extra: Partial<TaskRefHit> = {}): TaskRefHit {
  return { id: idOf(n), task_number: n, title, status_name: "Backlog", category: "backlog", archived: false, ...extra };
}

/** The owner's reply, as the screenshot shows it. */
const OWNER_REPLY = [
  "Done. Nine tasks are now in the **Metorite** project, all in Backlog, in the order they depend on each other:",
  "",
  ...NINE.map((n) => `- #${n} ${TITLES[n]} — ${n === 234 ? "decide nothing exists before a sandbox spins up." : "the next step."}`),
  "",
  "Want me to link the dependency chain (#236 blocks #237, etc.), or move #234 into To do to start it?",
].join("\n");

beforeAll(async () => {
  vi.stubGlobal("fetch", async () =>
    new Response(JSON.stringify({
      rows: [...NINE.map((n) => row(n, TITLES[n])), row(247, T247), row(248, T248)],
    }), { status: 200 }),
  );
  // The session's answers and the cards (scope "") read the same store.
  for (const n of NINE) taskRefResolver.request(SESSION, n);
  for (const n of [247, 248]) taskRefResolver.request("", n);
  await taskRefResolver.flush();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  push.mockClear();
  activations.length = 0;
});

function turn(content: string, author: string): ChatMessage {
  return {
    id: "m1",
    role: "assistant",
    content,
    timestamp: new Date("2026-10-19T10:00:00Z").getTime(),
    toolEvents: [],
    ...agentAuthor(author),
  } as unknown as ChatMessage;
}

/** The main chat: `AgentChat` passes the session's agent as `taskRefAgent`. */
function mainChat(content: string, agent = "orchestrator"): string {
  return renderToStaticMarkup(
    createElement(MessageBubble, {
      message: turn(content, agent),
      sessionId: SESSION,
      entityPills: false,
      taskRefAgent: agent,
    }),
  );
}

const hrefs = (html: string) => [...html.matchAll(/href="\/projects\?task=([^"]+)"/g)].map((m) => m[1]);

describe("cause 1: the main chat's answer links its task numbers", () => {
  it("draws the owner's nine tasks as nine pills that open the task", () => {
    const html = mainChat(OWNER_REPLY);
    const linked = hrefs(html);
    expect(new Set(linked)).toEqual(new Set(NINE.map(idOf)));
    // #234 twice and #236, #237 again in the last line: twelve pills.
    expect(linked).toHaveLength(12);
    // The pill shows the number, and the title stays in the text after it.
    expect(html).toContain("</a> Spec tool-gap detection — decide nothing");
    expect(html).not.toMatch(/>Spec tool-gap detection<\/span><\/a>/);
  });

  it("opens the task on a plain click", () => {
    mainChat(OWNER_REPLY);
    const link = activations.find((a) => a.href === `/projects?task=${idOf(234)}`);
    expect(link).toBeDefined();
    link!.go();
    expect(push).toHaveBeenCalledWith(`/projects?task=${idOf(234)}`);
  });

  it("also under the orchestrator's old name, and in the assistant dock's sessions", () => {
    expect(hrefs(mainChat("Next: #235.", "metorite"))).toEqual([idOf(235)]);
  });

  it("still not an email agent's answer", () => {
    expect(hrefs(mainChat("Ticket #235 is closed.", "email-assistant"))).toEqual([]);
  });

  it("links the «guillemet» form in the main chat too", () => {
    expect(hrefs(mainChat(`Made #235 «${TITLES[235]}».`))).toEqual([idOf(235)]);
  });

  it("stays text while the answer streams", () => {
    const html = renderToStaticMarkup(
      createElement(MessageBubble, {
        message: { ...turn("Made #235 and #236", "orchestrator"), streaming: true } as ChatMessage,
        sessionId: SESSION,
        taskRefAgent: "orchestrator",
      }),
    );
    expect(hrefs(html)).toEqual([]);
  });
});

describe("cause 2: a card's task names open the task", () => {
  // `link_tasks` in `skill_projects/writes.py`, as `_fields_block` prints it.
  const card = () =>
    renderToStaticMarkup(
      createElement(ConfirmationCard, {
        title: "Link these tasks?",
        detail: `#247 «${T247}» blocks #248 «${T248}»`,
        context: [
          "This change is recorded as yours, made through the Projects assistant.",
          "link_type: «blocks»",
          `from: #247 «${T247}»`,
          `to: #248 «${T248}»`,
        ].join("\n"),
        fenced: true,
        onApprove: () => {},
        onReject: () => {},
      }),
    );

  it("draws From and To as the task pills, and the detail line too", () => {
    const linked = hrefs(card());
    expect(linked.filter((id) => id === idOf(247)).length).toBeGreaterThanOrEqual(2);
    expect(linked.filter((id) => id === idOf(248)).length).toBeGreaterThanOrEqual(2);
  });

  it("opens the task from the From row", () => {
    card();
    const link = activations.find((a) => a.href === `/projects?task=${idOf(247)}`);
    link!.go();
    expect(push).toHaveBeenCalledWith(`/projects?task=${idOf(247)}`);
  });

  it("keeps the plain chip when the title does not match the task", () => {
    const html = renderToStaticMarkup(createElement(FencedText, { text: "#247 «Something else»", pills: false }));
    expect(hrefs(html)).toEqual([]);
    expect(html).toContain("Something else");
    expect(html).not.toContain("«");
  });

  it("draws no link inside a control (a fold's toggle)", () => {
    const html = renderToStaticMarkup(
      createElement(FencedText, { text: `#247 «${T247}» is done.`, pills: false, links: false }),
    );
    expect(hrefs(html)).toEqual([]);
    expect(html).toContain("data-fenced-name");
  });

  it("keeps a name with no number as quiet text", () => {
    const html = renderToStaticMarkup(createElement(FencedText, { text: `«${T247}»`, pills: false }));
    expect(hrefs(html)).toEqual([]);
    expect(html).toContain("data-fenced-name");
  });
});

describe("cause 3: a miss does not outlive a write in the same session", () => {
  it("asks a missed number again after revalidate, and links it", async () => {
    let created = false;
    const resolver = createTaskRefResolver(async (numbers) =>
      created ? numbers.filter((n) => n === 250).map((n) => row(n, "New task")) : [],
    );
    resolver.request("s", 250);
    await resolver.flush();
    expect(resolver.get("s", 250)).toMatchObject({ state: "done", hits: [] });
    // The turn's tool created #250, then the answer named it.
    created = true;
    resolver.request("s", 250);
    await resolver.flush();
    expect(resolver.get("s", 250)).toMatchObject({ hits: [] }); // asked once is enough...
    resolver.revalidate();
    await resolver.flush();
    expect(resolver.get("s", 250)).toMatchObject({ state: "done", hits: [{ id: idOf(250) }] });
  });

  it("keeps the old answer while it asks again, and when that ask fails", async () => {
    let fail = false;
    const resolver = createTaskRefResolver(async () => {
      if (fail) throw new Error("down");
      return [row(251, "Kept")];
    }, []);
    resolver.request("s", 251);
    await resolver.flush();
    fail = true;
    resolver.revalidate("s");
    expect(resolver.get("s", 251)).toMatchObject({ state: "done" });
    await resolver.flush();
    expect(resolver.get("s", 251)).toMatchObject({ state: "done", hits: [{ title: "Kept" }] });
  });

  it("asks an old miss again when a pill mounts after the TTL", async () => {
    let clock = 0;
    let calls = 0;
    const resolver = createTaskRefResolver(async () => {
      calls += 1;
      return calls > 1 ? [row(252, "Late")] : [];
    }, [], () => clock, 30_000);
    resolver.request("s", 252);
    await resolver.flush();
    clock = 10_000;
    resolver.request("s", 252);
    await resolver.flush();
    expect(calls).toBe(1);
    clock = 31_000;
    resolver.request("s", 252);
    await resolver.flush();
    expect(calls).toBe(2);
    expect(resolver.get("s", 252)).toMatchObject({ hits: [{ title: "Late" }] });
  });

  it("revalidates when a tool of the turn finishes and when the turn ends, never for history", () => {
    // History: a finished turn mounts. Nothing.
    expect(revalidateStep(null, 3, false).revalidate).toBe(false);
    // A live turn: the tool finishes, then the answer ends.
    let s = revalidateStep(null, 0, true);
    expect(s.revalidate).toBe(false);
    s = revalidateStep(s.next, 1, true);
    expect(s.revalidate).toBe(true);
    s = revalidateStep(s.next, 1, true);
    expect(s.revalidate).toBe(false);
    s = revalidateStep(s.next, 1, false);
    expect(s.revalidate).toBe(true);
    // Once only.
    expect(revalidateStep(s.next, 1, false).revalidate).toBe(false);
    // A turn with no tool asks nothing when it ends.
    const quiet = revalidateStep(revalidateStep(null, 0, true).next, 0, false);
    expect(quiet.revalidate).toBe(false);
  });
});

describe("cause 4: two spaces with a #234", () => {
  const two = [row(234, "Spec tool-gap detection"), { ...row(234, "Order the filament"), id: idOf(999) }];

  it("links neither without a title, as before", () => {
    expect(pickTask(two)).toBeNull();
  });

  it("links the one whose title the text wrote after the number", () => {
    expect(pickTask(two, undefined, (t) => t === "Spec tool-gap detection")?.id).toBe(idOf(234));
  });

  it("links neither when the text echoes both or none", () => {
    expect(pickTask(two, undefined, () => true)).toBeNull();
    expect(pickTask(two, undefined, () => false)).toBeNull();
  });
});
