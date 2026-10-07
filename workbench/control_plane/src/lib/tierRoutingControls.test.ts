/**
 * The member chooses no effort and no agent (owner, 2026-10-07). The pure
 * rules of the amendment of D90 §5 and §7, in `ai_tier_routing.md`.
 *
 * `AgentChat.picker.test.ts` holds the composer markup that uses them. This
 * file holds the rules, and the wiring of `/chat` (`app/chat/page.tsx`), which
 * vitest in node cannot mount.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - the effort selector stays for a covered agent -> "a covered agent has no
 *   effort selector and no agent selector";
 * - a control draws before the agent list lands -> "no control flashes";
 * - a covered agent sends the member's old effort -> "a covered agent sends
 *   auto";
 * - the UI flag off changes a control -> "the UI flag off is as today";
 * - a new `/chat` conversation still opens the picker for a covered
 *   orchestrator -> "a covered orchestrator takes every new conversation";
 * - an existing conversation on another agent opens on the orchestrator ->
 *   "an existing conversation keeps its own agent";
 * - `?agent=` goes -> "the /agents deep link stays";
 * - the repair picker goes -> "a conversation with no known agent still draws
 *   the picker".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CHAT_AGENT,
  agentPickerShows,
  chatAgentChoice,
  composerControls,
  initialChatOpen,
  newChatAction,
  sentThinkMode,
  type ChatAgentChoice,
} from "./tierRouting";

const covered = { name: "orchestrator", tier_routed: true };
const uncovered = { name: "orchestrator", tier_routed: false };
const unstamped = { name: "orchestrator" };

describe("composerControls", () => {
  it("a covered agent has no effort selector and no agent selector", () => {
    expect(
      composerControls({ uiOn: true, agentsKnown: true, entry: covered, isOrchestrator: true }),
    ).toEqual({ showEffort: false, showAgentSwitch: false });
    expect(
      composerControls({ uiOn: true, agentsKnown: true, entry: covered, isOrchestrator: false }),
    ).toEqual({ showEffort: false, showAgentSwitch: false });
  });

  it("no control flashes while the agent list loads", () => {
    expect(
      composerControls({ uiOn: true, agentsKnown: false, entry: undefined, isOrchestrator: true }),
    ).toEqual({ showEffort: false, showAgentSwitch: false });
  });

  it("an agent that the gateway does not cover keeps both, as today", () => {
    for (const entry of [uncovered, unstamped, undefined]) {
      expect(
        composerControls({ uiOn: true, agentsKnown: true, entry, isOrchestrator: true }),
      ).toEqual({ showEffort: true, showAgentSwitch: true });
    }
  });

  it("the UI flag off is as today, whatever the gateway says", () => {
    for (const entry of [covered, uncovered, unstamped, undefined]) {
      for (const agentsKnown of [true, false]) {
        expect(
          composerControls({ uiOn: false, agentsKnown, entry, isOrchestrator: true }),
        ).toEqual({ showEffort: true, showAgentSwitch: true });
        // Only the orchestrator has an agent selector, as today.
        expect(
          composerControls({ uiOn: false, agentsKnown, entry, isOrchestrator: false }),
        ).toEqual({ showEffort: true, showAgentSwitch: false });
      }
    }
  });
});

describe("sentThinkMode", () => {
  it("a covered agent sends auto, also after a choice made on another agent", () => {
    expect(sentThinkMode(true, "max")).toBe("auto");
    expect(sentThinkMode(true, "thinking")).toBe("auto");
    expect(sentThinkMode(true, "auto")).toBe("auto");
  });

  it("an agent that is not covered sends the member's choice, as today", () => {
    expect(sentThinkMode(false, "max")).toBe("max");
    expect(sentThinkMode(false, "thinking")).toBe("thinking");
    expect(sentThinkMode(false, "auto")).toBe("auto");
  });
});

describe("chatAgentChoice and the new-conversation button", () => {
  it("a covered orchestrator takes every new conversation, with no picker", () => {
    const choice = chatAgentChoice({ uiOn: true, agentsKnown: true, entries: [covered] });
    expect(choice).toBe("orchestrator");
    expect(newChatAction(choice)).toBe("create");
    expect(agentPickerShows({ requested: true, choice, repairing: false })).toBe(false);
    expect(CHAT_AGENT).toBe("orchestrator");
  });

  it("the UI flag off is as today: the member picks", () => {
    for (const entries of [[covered], [uncovered], []]) {
      for (const agentsKnown of [true, false]) {
        const choice = chatAgentChoice({ uiOn: false, agentsKnown, entries });
        expect(choice).toBe("member");
        expect(newChatAction(choice)).toBe("picker");
        expect(agentPickerShows({ requested: true, choice, repairing: false })).toBe(true);
      }
    }
  });

  it("an orchestrator that is not covered keeps the picker", () => {
    expect(chatAgentChoice({ uiOn: true, agentsKnown: true, entries: [uncovered] })).toBe("member");
    expect(chatAgentChoice({ uiOn: true, agentsKnown: true, entries: [unstamped] })).toBe("member");
    // Another covered agent does not take the choice away.
    expect(
      chatAgentChoice({
        uiOn: true, agentsKnown: true,
        entries: [uncovered, { name: "projects-assistant", tier_routed: true }],
      }),
    ).toBe("member");
  });

  it("waits for the agent list, and draws no picker meanwhile", () => {
    const choice = chatAgentChoice({ uiOn: true, agentsKnown: false, entries: [] });
    expect(choice).toBe("pending");
    expect(newChatAction(choice)).toBe("wait");
    expect(agentPickerShows({ requested: true, choice, repairing: false })).toBe(false);
  });

  it("a conversation with no known agent still draws the picker, with any choice", () => {
    for (const choice of ["member", "orchestrator", "pending"] as ChatAgentChoice[]) {
      expect(agentPickerShows({ requested: true, choice, repairing: true })).toBe(true);
      expect(agentPickerShows({ requested: false, choice, repairing: true })).toBe(false);
    }
  });
});

describe("initialChatOpen", () => {
  const sessions = [
    { id: "s-projects", agentName: "projects-assistant" },
    { id: "s-orch", agentName: "orchestrator" },
  ];

  it("an existing conversation keeps its own agent", () => {
    // The latest conversation is on projects-assistant, and it opens as it is.
    expect(initialChatOpen({ sessions, agentParam: null })).toEqual({ kind: "open", id: "s-projects" });
  });

  it("the /agents deep link stays: ?agent= opens or creates that agent's conversation", () => {
    expect(initialChatOpen({ sessions, agentParam: "orchestrator" })).toEqual({ kind: "open", id: "s-orch" });
    expect(initialChatOpen({ sessions, agentParam: "crm-assistant" })).toEqual({
      kind: "create", agent: "crm-assistant",
    });
  });

  it("opens nothing when there is no conversation", () => {
    expect(initialChatOpen({ sessions: [], agentParam: undefined })).toEqual({ kind: "none" });
  });
});

describe("the /chat wiring", () => {
  const read = (rel: string) => readFileSync(join(__dirname, rel), "utf-8");
  const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*/g, "");
  const page = code(read("../app/chat/page.tsx"));

  it("an existing conversation opens with the agent it was stored with", () => {
    expect(page).toMatch(/<AgentChat[\s\S]*?agentName=\{activeSession\.agentName\}/);
    expect(page).toMatch(/initialChatOpen\(\{\s*sessions: existing,\s*agentParam: searchParams\?\.get\("agent"\),/);
  });

  it("the picker draws only through agentPickerShows", () => {
    expect(page).toMatch(/\{pickerShows && \(\s*<AgentPickerModal/);
    expect(page).not.toMatch(/\{showPicker && \(/);
  });

  it("a new orchestrator conversation never repairs the active one", () => {
    const start = page.slice(page.indexOf("const startOrchestratorChat"), page.indexOf("const newChatWaitingRef"));
    expect(start).toMatch(/createSession\(CHAT_AGENT\)/);
    expect(start).not.toMatch(/isUnresolvedAgent/);
  });

  it("the composer sends think_mode through sentThinkMode", () => {
    const chat = code(read("../components/AgentChat.tsx"));
    expect(chat).toMatch(/thinkMode: sentThinkMode\(modelPlan\.covered, thinkMode\)/);
    expect(chat).toMatch(/\{controls\.showAgentSwitch && \(/);
    expect(chat).toMatch(/\{controls\.showEffort && \(/);
    expect(chat).not.toMatch(/\{isOrchestrator && /);
  });
});
