// The agent's working trail, in words, on every chat surface (owner ask,
// 2026-10-05: "Add this detailed step-by-step context to the Projects app
// assistant").
//
// R7 fences named here:
//   * `trail-step-words`: `describeToolStep` says what a step did, the way a
//     person would ("Ran a script in the sandbox", "Asked Email assistant",
//     "Created a task", "Wrote report.md", "Read file outputs/…"), in the
//     running, done and failed tenses.
//   * `trail-rail-renders-steps`: a recorded event stream, folded by the SAME
//     reducer both SSE loops use (`lib/chatStream.ts`), renders through the
//     SAME per-turn component every rail mounts (`MessageBubble`, inside the
//     shared `AgentChat`) as one row per step, with its status.
//   * `trail-run-output`: a script step shows its command and its output when
//     it is open, and not when it is closed. A long output is cut, and says so.
//   * `trail-args-cap`: a running row learns its arguments only once they
//     are a whole object, and a long value is cut to 8 KB with a marker. A
//     long command is cut in the detail too.
//   * `trail-args-full`: a step's detail shows every argument value in full,
//     with its key as a card label ("Task", never "task:"), in the trail's
//     type. A long value opens clamped behind "Show more", and a credential
//     shows as "Hidden". A hand-off names its agent in words ("Email
//     assistant", never `email_assistant`), and shows its task as the step's
//     description (owner report, 2026-10-09).
//   * `trail-one-component`: the Projects, Tasks and email rails mount the
//     shared `AgentChat`, so none of them can draw a second, thinner trail.

import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  CLAMP_CHARS,
  TOOL_ARGS_CAP,
  argFields,
  capToolArgs,
  needsClamp,
  wholeToolArgs,
} from "@/lib/toolArgs";
import { agentName } from "@/lib/agentName";
import { fieldSpec } from "@/lib/cardFields";
import {
  COMMAND_CAP,
  OUTPUT_CAP,
  bareToolName,
  capOutput,
  describeToolStep,
  splitAgentLabel,
} from "@/lib/toolSteps";
import { applyStreamEvent, applySubAgentEvent, type StreamFold } from "@/lib/chatStream";
import type { ChatMessage } from "@/hooks/useAgentChat";
import type { ToolEvent } from "@/components/MarkdownMessage";
import MessageBubble from "@/components/MessageBubble";
import { ToolStepRow } from "@/components/ThinkingContainer";

// A Projects card in the turn opens a task through the router.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// ─── trail-step-words ───────────────────────────────────────────────────────

describe("describeToolStep — a step in words", () => {
  const step = (name: string, args: Record<string, unknown> = {}, status: ToolEvent["status"] = "done") =>
    describeToolStep({ name, args, status });

  it("names a script in the sandbox, with its command as the target", () => {
    expect(step("run_command", { command: "python make_chart.py --q3" })).toEqual({
      kind: "run",
      status: "done",
      label: "Ran a script in the sandbox",
      target: "python make_chart.py --q3",
    });
    expect(step("run_command", { command: "ls" }, "running").label).toBe("Running a script in the sandbox");
    expect(step("run_command", { command: "ls" }, "error").status).toBe("failed");
  });

  it("names a file read with its path, and a file write with its name", () => {
    expect(step("read_file", { path: "outputs/data.csv" }).label).toBe("Read file outputs/data.csv");
    expect(step("read_file", { path: "outputs/q3/sales/data.csv" }).label).toBe("Read file …/sales/data.csv");
    expect(step("write_artifact", { path: "report.md" }).label).toBe("Wrote report.md");
    expect(step("write_artifact", { path: "report.md" }, "running").label).toBe("Writing report.md");
  });

  it("names a hand-off by the agent it asked", () => {
    expect(step("call_agent", { agent_name: "email-assistant", task: "find the PO" }).label).toBe(
      "Asked Email assistant",
    );
    expect(
      describeToolStep({ name: "call_agent", status: "running", subAgentName: "email-assistant" }).label,
    ).toBe("Asking Email assistant");
  });

  it("names the Projects tools as acts on tasks and projects", () => {
    expect(step("create_task", { title: "Ship it" }).label).toBe("Created a task");
    expect(step("update_task").label).toBe("Updated a task");
    expect(step("create_personal_task").label).toBe("Created a personal task");
    expect(step("list_tasks").label).toBe("Listed tasks");
    expect(step("task_detail").label).toBe("Read a task");
    expect(step("analytics_stuck").label).toBe("Checked what is stuck");
    expect(step("render_board").label).toBe("Drew a board");
    expect(step("report_save").label).toBe("Saved a report");
    expect(step("archive_project", {}, "running").label).toBe("Archiving a project");
    expect(step("search_emails").label).toBe("Searched emails");
    // WS-46 P13: the batch reads as one act on a project, never "a tasks".
    expect(step("create_tasks").label).toBe("Added tasks to a project");
    expect(step("create_tasks", {}, "running").label).toBe("Adding tasks to a project");
    expect(step("create_tags").label).toBe("Added tags to a project");
    expect(step("create_types", {}, "running").label).toBe("Adding task types to a project");
  });

  it("keys on the bare name when a runtime prefixes it", () => {
    expect(bareToolName("skill_projects.create_task")).toBe("create_task");
    expect(step("skill_projects.create_task").label).toBe("Created a task");
  });

  it("still draws a tool it does not know, by its name", () => {
    expect(step("frobnicate_widget").label).toBe("Used frobnicate widget");
  });
});

// ─── trail-rail-renders-steps ───────────────────────────────────────────────

/**
 * A recorded run, in the shape `api/agent/chat/route.ts` forwards to the
 * browser: the Projects agent reads an email through email-assistant, runs a
 * script, writes a report, and one step fails while another still runs.
 */
const RECORDED: Array<Record<string, unknown>> = [
  { type: "tool_start", id: "t1", name: "call_agent", args: {} },
  { type: "sub_agent_tool_start", agentName: "email-assistant", id: "s1", name: "read_email" },
  { type: "sub_agent_tool_end", agentName: "email-assistant", id: "s1", result: "PO 4471 from Acme", success: true },
  { type: "sub_agent_delta", agentName: "email-assistant", delta: "The PO is 4471." },
  {
    type: "tool_end", id: "t1", name: "call_agent",
    args: { agent_name: "email-assistant", task: "Find the PO in my inbox" },
    result: "The PO is 4471.", success: true,
  },
  { type: "tool_start", id: "t2", name: "run_command", args: {} },
  { type: "tool_partial", id: "t2", result: "rows: 42\n" },
  {
    type: "tool_end", id: "t2", name: "run_command",
    args: { command: "python summarise.py outputs/po.csv" },
    result: "rows: 42\ntotal: 18,400", success: true,
  },
  { type: "tool_start", id: "t3", name: "create_task", args: {} },
  {
    type: "tool_end", id: "t3", name: "create_task",
    args: { title: "Chase PO 4471" }, result: "403: not allowed", success: false,
  },
  { type: "tool_start", id: "t4", name: "write_artifact", args: {} },
  // The arguments arrive whole while the step still runs (route.ts).
  { type: "tool_args", id: "t4", args: { path: "outputs/report.md" } },
];

function replay(events: Array<Record<string, unknown>>): ChatMessage {
  const fold: StreamFold = { foldedAnswerIdx: -1 };
  let m: ChatMessage = {
    id: "a1", role: "assistant", content: "", timestamp: 0,
    streaming: true, toolEvents: [], progressLines: [], isThinkingActive: true,
    authorKind: "agent", authorEmail: "projects-assistant",
  };
  for (const evt of events) {
    m = String(evt.type).startsWith("sub_agent_")
      ? applySubAgentEvent(m, evt)
      : applyStreamEvent(m, evt, fold);
  }
  return m;
}

/** Every step row in the rendered turn, as `[status, text]`. */
function rows(html: string): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  const re = /<div class="[^"]*" data-step-status="(\w+)" data-step-kind="\w+">([\s\S]*?)<\/button>/g;
  for (let m = re.exec(html); m; m = re.exec(html)) {
    out.push([m[1], m[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()]);
  }
  return out;
}

describe("the rail renders the agent's steps from a recorded stream", () => {
  const html = renderToStaticMarkup(
    createElement(MessageBubble, {
      message: replay(RECORDED),
      sessionId: "s-1",
      sessionAgentName: "projects-assistant",
      entityPills: true,
    }),
  );

  it("draws one row per step, in order, with its words", () => {
    expect(rows(html).map(([, text]) => text)).toEqual([
      "Asked Email assistant done ▾",
      "Ran a script in the sandbox python summarise.py outputs/po.csv done ▾",
      "Created a task failed failed ▾",
      "Writing report.md outputs/report.md running ▴",
    ]);
  });

  it("gives each step its status: done, failed, running", () => {
    expect(rows(html).map(([status]) => status)).toEqual(["done", "done", "failed", "running"]);
  });

  it("names the step in flight in the trail's header", () => {
    expect(html).toContain("Writing report.md");
    expect(html).not.toContain("Thinking…");
  });
});

// ─── trail-run-output ───────────────────────────────────────────────────────

describe("a script step shows its command and output when open", () => {
  const ran = replay(RECORDED).toolEvents!.find((t) => t.id === "t2")!;

  it("shows nothing of the output while closed", () => {
    const closed = renderToStaticMarkup(createElement(ToolStepRow, { event: ran, open: false }));
    expect(closed).not.toContain("total: 18,400");
    expect(closed).not.toContain("data-step-output");
  });

  it("shows the command and the output once opened", () => {
    const open = renderToStaticMarkup(createElement(ToolStepRow, { event: ran, open: true }));
    expect(open).toContain("data-step-output");
    expect(open).toContain("total: 18,400");
    expect(open).toContain("summarise.py");
  });

  it("shows a hand-off's own steps under it", () => {
    const asked = replay(RECORDED).toolEvents!.find((t) => t.id === "t1")!;
    const open = renderToStaticMarkup(createElement(ToolStepRow, { event: asked, open: true }));
    // The branch's own header names the agent in words, not its slug.
    expect(open).toMatch(/font-medium truncate">Email assistant</);
    expect(open).toContain("Read an email");
  });

  it("cuts a long output, and says how much it left out", () => {
    const long: ToolEvent = { ...ran, result: "x".repeat(OUTPUT_CAP + 250) };
    const open = renderToStaticMarkup(createElement(ToolStepRow, { event: long, open: true }));
    expect(open).not.toContain("x".repeat(OUTPUT_CAP + 1));
    expect(open).toContain("250 more characters not shown");
    expect(capOutput("abc", 2)).toEqual({ text: "ab", cut: 1 });
  });
});

// ─── trail-args-cap ─────────────────────────────────────────────────────────

describe("the arguments a running row learns", () => {
  it("are forwarded only once they are a whole object", () => {
    expect(wholeToolArgs('{"command": "ls')).toBeNull();
    expect(wholeToolArgs('{"command": "ls"} ')).toEqual({ command: "ls" });
    expect(wholeToolArgs("{}")).toBeNull();
    expect(wholeToolArgs("[1]")).toBeNull();
  });

  it("are cut to the cap, and say so", () => {
    const body = "y".repeat(TOOL_ARGS_CAP + 500);
    const out = wholeToolArgs(JSON.stringify({ path: "a.md", content: body }))!;
    expect(out.path).toBe("a.md");
    expect(String(out.content).length).toBeLessThan(TOOL_ARGS_CAP + 100);
    expect(String(out.content)).toMatch(/\[cut: \d+ more characters\]$/);
    expect(capToolArgs({ rows: Array(5000).fill("z") }).rows).toMatch(/^\[cut: \d+ characters\]$/);
  });

  it("a long command is cut in the detail, and says so", () => {
    const long: ToolEvent = {
      id: "x", name: "run_command", status: "running",
      args: { command: "echo " + "q".repeat(COMMAND_CAP + 40) },
    };
    const open = renderToStaticMarkup(createElement(ToolStepRow, { event: long, open: true }));
    expect(open).not.toContain("q".repeat(COMMAND_CAP));
    expect(open).toContain("45 more characters not shown");
  });
});

// ─── trail-one-component ────────────────────────────────────────────────────

describe("every rail mounts the shared chat, so the trail is the same one", () => {
  const SRC = path.resolve(__dirname, "..");
  const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf-8");

  it.each([
    "app/projects/components/AssistantRail.tsx",
    "app/tasks/components/AssistantRail.tsx",
    "app/email/components/EmailAssistantChat.tsx",
  ])("%s renders <AgentChat>", (rel) => {
    const src = read(rel);
    expect(src).toMatch(/import AgentChat from "@\/components\/AgentChat"/);
    expect(src).toMatch(/<AgentChat\b/);
  });

  it("the shared chat's working line names the step through the one vocabulary", () => {
    expect(read("components/AgentChat.tsx")).toMatch(/describeToolStep\(running\)\.label/);
  });
});

// ─── trail-args-full ────────────────────────────────────────────────────────

/**
 * The owner's report (2026-10-09). On /chat, the orchestrator asked
 * email-assistant through the agent's own tool, `email_assistant`. The open
 * step showed `email_assistant`, the raw name, and then
 * `task: In the Fracktal mailbox (vjvarada@fracktal.in), find the email
 * thread related to`, cut at 80 characters with no ellipsis and no way to
 * read the rest (`ArgsDetail`, `.slice(0, 80)`).
 */
const OWNER_TASK =
  "In the Fracktal mailbox (vjvarada@fracktal.in), find the email thread related to " +
  "the Welmont School lab order. Read every message in the thread, list who sent what " +
  "and when, and say which questions are still open. Then draft a short reply to the " +
  "latest message that answers the delivery question, and do not send it.";

const OWNER_STEP: ToolEvent = {
  id: "o1",
  name: "email_assistant",
  status: "done",
  args: { task: OWNER_TASK },
  result: "Found the thread: 6 messages.",
  startedAt: 1000,
  endedAt: 4200,
};

describe("a step's arguments show in full, in words", () => {
  const label = (k: string) => fieldSpec(k).label;

  it("never cuts a value: a long one is whole, and opens clamped", () => {
    expect(OWNER_TASK.length).toBeGreaterThan(280);
    const [f] = argFields({ task: OWNER_TASK }, label);
    expect(f.text).toBe(OWNER_TASK);
    expect(f.clamp).toBe(true);
  });

  it("clamps past three lines or about three lines of text, and not before", () => {
    expect(needsClamp("x".repeat(CLAMP_CHARS))).toBe(false);
    expect(needsClamp("x".repeat(CLAMP_CHARS + 1))).toBe(true);
    expect(needsClamp(["a", "b", "c"].join("\n"))).toBe(false);
    expect(needsClamp(["a", "b", "c", "d"].join("\n"))).toBe(true);
    expect(argFields({ query: "PO 4471" }, label)[0].clamp).toBe(false);
  });

  it("names each key in product words, from the card labels", () => {
    const fields = argFields({ task: "a b", due_at: "2026-10-09", account_id: "a1", tags: ["ops", "bug"] }, label);
    expect(fields.map((f) => f.label)).toEqual(["Task", "Due", "Account id", "Tags"]);
    expect(fields[3].text).toBe("ops, bug");
    expect(argFields({ _raw: '{"x": ' }, label)[0].label).toBe("Arguments");
  });

  it("keeps a credential off the screen", () => {
    const fields = argFields({ password: "hunter2", api_key: "sk-1", access_token: "t", max_tokens: 50 }, label);
    expect(fields.filter((f) => f.hidden).map((f) => f.key)).toEqual(["password", "api_key", "access_token"]);
    expect(fields.every((f) => !f.hidden || f.text === "")).toBe(true);
    expect(fields.find((f) => f.key === "max_tokens")?.text).toBe("50");
  });

  it("leaves out the keys the row already shows, and empty values", () => {
    expect(argFields({ task: "a b", note: "", extra: "kept" }, label, ["task"]).map((f) => f.key)).toEqual(["extra"]);
  });
});

describe("a hand-off names its agent in words, and shows its task", () => {
  it("reads an agent slug as words, in either spelling", () => {
    expect(agentName("email_assistant")).toBe("Email assistant");
    expect(agentName("email-assistant")).toBe("Email assistant");
    expect(agentName("projects-assistant")).toBe("Projects assistant");
  });

  it("reads the orchestrator's specialist tool as a hand-off, with the task in full", () => {
    const step = describeToolStep(OWNER_STEP);
    expect(step).toMatchObject({
      kind: "delegate",
      label: "Asked Email assistant",
      agent: "Email assistant",
      description: OWNER_TASK,
      shownKeys: ["task"],
    });
    expect(splitAgentLabel(step)).toEqual({ lead: "Asked", agent: "Email assistant" });
    expect(describeToolStep({ ...OWNER_STEP, status: "running" }).label).toBe("Asking Email assistant");
  });

  it("reads call_agent's message as the task", () => {
    const step = describeToolStep({
      name: "call_agent", status: "done",
      args: { agent_name: "crm-assistant", message: "Find the Acme deal" },
    });
    expect(step).toMatchObject({ agent: "Crm assistant", description: "Find the Acme deal" });
    expect(step.shownKeys).toEqual(["agent_name", "message"]);
  });

  it("does not take a tool with a task reference for a hand-off", () => {
    expect(describeToolStep({ name: "frobnicate", status: "done", args: { task: "#12" } }).kind).toBe("other");
  });

  it("does not take the platform tools with a task argument for agents (review round 1)", () => {
    const code = describeToolStep({ name: "code_task", status: "done", args: { task: "Write a script that parses the CSV" } });
    expect(code).toMatchObject({ kind: "other", label: "Ran a coding task" });
    expect(code.agent).toBeUndefined();
    const spawn = describeToolStep({ name: "spawn_copilot_agent", status: "done", args: { task: "Fix the build" } });
    expect(spawn.label).toBe("Asked a Copilot agent");
    expect(spawn.agent).toBeUndefined();
  });

  it("draws no empty detail box for a running hand-off (review round 1)", () => {
    const html = renderToStaticMarkup(createElement(ToolStepRow, {
      event: { ...OWNER_STEP, status: "running", result: undefined, endedAt: undefined },
      open: true,
    }));
    expect(html).not.toMatch(/bg-card\/40[^"]*"><\/div>/);
    expect(html).toContain(OWNER_TASK);
  });

  it("never cuts a sub-agent step's result silently (review round 1)", () => {
    const said = "PO 4471 from Acme, due on 30 October, for the Welmont School lab: 12 benches, 4 fume hoods";
    const html = renderToStaticMarkup(createElement(ToolStepRow, {
      event: {
        id: "d", name: "call_agent", status: "done", args: { agent_name: "email-assistant", message: "Find it" },
        subAgentName: "email-assistant",
        subAgentTools: [{ id: "s", name: "read_email", status: "done", result: said }],
      },
      open: true,
    }));
    expect(html).toContain(said);
  });

  it("draws the owner's step: the agent chip, the whole task once, a toggle, no raw names", () => {
    for (const open of [false, true]) {
      const html = renderToStaticMarkup(createElement(ToolStepRow, { event: OWNER_STEP, open }));
      expect(html).toContain(OWNER_TASK); // whole, never cut
      expect(html.split(OWNER_TASK).length - 1).toBe(1); // the detail does not repeat it
      expect(html).toContain("data-step-description");
      expect(html).toContain("data-clamped");
      expect(html).toContain("Show more");
      expect(html).toContain(">Email assistant<");
      expect(html).not.toContain("email_assistant");
      expect(html).not.toContain("task:");
    }
  });

  it("draws the other arguments as labels and values, in the trail's type", () => {
    const html = renderToStaticMarkup(createElement(ToolStepRow, {
      event: {
        id: "q", name: "query_inbox", status: "done",
        args: { query: "Welmont " + "order ".repeat(60), due_at: "2026-10-09" },
      },
      open: true,
    }));
    const args = /<dl data-step-args=""[^>]*>([\s\S]*?)<\/dl>/.exec(html)?.[1] ?? "";
    expect(args).toContain(">Query<");
    expect(args).toContain(">Due<");
    expect(args).toContain("order ".repeat(60).trim());
    expect(args).not.toContain("font-mono");
    expect(html).toContain("Show more");
  });
});
