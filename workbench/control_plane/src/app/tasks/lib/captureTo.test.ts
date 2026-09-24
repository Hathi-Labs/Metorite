/**
 * S6g — `#` parsing, the capture-to-project flow, one promote path, and the
 * deferred-commit Undo (my_tasks_cutover.md §5 S6g).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    apiCapture: vi.fn(),
    apiMoveTask: vi.fn(),
    apiOrganize: vi.fn(),
    fetchProjects: vi.fn(),
    fetchMyRoot: vi.fn(),
    fetchAreas: vi.fn(),
    fetchUntriaged: vi.fn(),
  };
});
vi.mock("./lens", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lens")>();
  return { ...actual, lensGetItem: vi.fn() };
});
vi.mock("@/app/projects/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/app/projects/lib/api")>();
  return {
    ...actual,
    projectsApi: { ...actual.projectsApi, previewMove: vi.fn() },
  };
});

import { projectsApi } from "@/app/projects/lib/api";

import { apiCapture, apiMoveTask, apiOrganize, fetchAreas, fetchUntriaged } from "./api";
import { lensGetItem } from "./lens";
import {
  PROMOTE_HINT,
  PROMOTE_UNDO_MS,
  deferCommit,
  promotePendingToast,
  promoteToast,
} from "./promote";
import { type CaptureDestination, openHashQuery, parseProjectToken, stripOpenHash } from "./quickAdd";
import { useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

const read = (rel: string) =>
  readFileSync(resolve(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

const task = (id: string, over: Partial<MyTask> = {}): MyTask => ({
  id,
  source: "LOCAL",
  title: `Task ${id}`,
  disposition: "INBOX",
  isMine: true,
  createdAt: "2026-09-20T09:00:00Z",
  updatedAt: "2026-09-20T09:00:00Z",
  projectId: "root-1",
  ...over,
});

const DESTS: CaptureDestination[] = [
  { id: "a1", name: "Home", kind: "area" },
  { id: "p1", name: "Printer v3", kind: "project" },
  { id: "p2", name: "Printer", kind: "project" },
  { id: "p3", name: "Payroll", kind: "project" },
];

// ── `#` ──────────────────────────────────────────────────────────────────────

describe("parseProjectToken", () => {
  it("takes the longest run of words that names one destination", () => {
    expect(parseProjectToken("Fix the jam #Printer v3", DESTS)).toEqual({
      title: "Fix the jam",
      match: DESTS[1],
      query: "Printer v3",
    });
  });

  it("an exact name wins over the prefix of a longer one, and the rest is title", () => {
    const out = parseProjectToken("#printer fix the jam", DESTS);
    expect(out.match?.id).toBe("p2");
    expect(out.title).toBe("fix the jam");
  });

  it("matches a unique prefix, ignoring case", () => {
    expect(parseProjectToken("Run #pay today", DESTS).match?.id).toBe("p3");
    expect(parseProjectToken("Water plants #HO", DESTS).match).toEqual(DESTS[0]);
  });

  it("an ambiguous or unknown token leaves the line alone and reports the query", () => {
    expect(parseProjectToken("Fix #P thing", DESTS)).toEqual({
      title: "Fix #P thing",
      query: "P thing",
    });
    expect(parseProjectToken("Fix #Nowhere", DESTS).match).toBeUndefined();
  });

  it("a # inside a word is text", () => {
    expect(parseProjectToken("Learn C# basics", DESTS)).toEqual({ title: "Learn C# basics" });
  });

  it("knows the fragment being typed, and strips it on a pick", () => {
    expect(openHashQuery("Fix the jam #Pri")).toBe("Pri");
    expect(openHashQuery("Fix the jam #")).toBe("");
    expect(openHashQuery("Learn C#")).toBeNull();
    expect(stripOpenHash("Fix the jam #Pri")).toBe("Fix the jam");
  });
});

// ── the deferred commit ───────────────────────────────────────────────────────

describe("deferCommit — the promote Undo", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("runs after the delay", () => {
    const run = vi.fn();
    deferCommit(run);
    vi.advanceTimersByTime(PROMOTE_UNDO_MS - 1);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("Undo before the delay cancels it, and nothing runs", () => {
    const run = vi.fn();
    const c = deferCommit(run);
    expect(c.cancel()).toBe(true);
    vi.advanceTimersByTime(PROMOTE_UNDO_MS * 2);
    expect(run).not.toHaveBeenCalled();
  });

  it("after it ran, Undo is refused", () => {
    const run = vi.fn();
    const c = deferCommit(run);
    vi.advanceTimersByTime(PROMOTE_UNDO_MS);
    expect(c.cancel()).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("waits about five seconds", () => {
    expect(PROMOTE_UNDO_MS).toBe(5000);
  });
});

// ── the store's one deferred promote ─────────────────────────────────────────

describe("schedulePromote / undoPromote", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useTaskStore.setState({
      items: [task("t1")],
      projects: [{ id: "p1", source: "LOCAL", outcome: "Printer v3", status: "ACTIVE", hasNextAction: false }],
      pendingPromote: null,
      promoteNotice: null,
      syncFailure: null,
    });
  });
  afterEach(() => {
    // The pending promote is module state: cancel what a test left behind.
    useTaskStore.getState().undoPromote();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("sends after the delay, then says Moved to", async () => {
    const commit = vi.fn().mockResolvedValue({ left: false, item: task("t1", { projectId: "p1" }) });
    useTaskStore.getState().schedulePromote({ id: "t1", projectName: "Printer v3", commit });
    expect(useTaskStore.getState().pendingPromote).toMatchObject({ id: "t1", sending: false });
    vi.advanceTimersByTime(PROMOTE_UNDO_MS - 1);
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(commit).toHaveBeenCalledTimes(1);
    await vi.runAllTimersAsync();
    const notice = useTaskStore.getState().promoteNotice;
    expect(notice && "title" in notice ? notice.title : null).toBe("Moved to Printer v3");
    expect(useTaskStore.getState().pendingPromote).toBeNull();
  });

  it("Undo cancels before the send, and nothing reaches the server", () => {
    const commit = vi.fn();
    const onCancel = vi.fn();
    useTaskStore.getState().schedulePromote({ id: "t1", projectName: "Printer v3", commit, onCancel });
    expect(useTaskStore.getState().undoPromote()).toBe(true);
    vi.advanceTimersByTime(PROMOTE_UNDO_MS * 2);
    expect(commit).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(useTaskStore.getState().pendingPromote).toBeNull();
  });

  it("after the send there is no Undo (D62)", async () => {
    const commit = vi.fn().mockResolvedValue({ left: false, item: task("t1") });
    useTaskStore.getState().schedulePromote({ id: "t1", projectName: "Printer v3", commit });
    vi.advanceTimersByTime(PROMOTE_UNDO_MS);
    expect(useTaskStore.getState().undoPromote()).toBe(false);
    await vi.runAllTimersAsync();
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("a second promote sends the first at once", () => {
    const first = vi.fn().mockResolvedValue({ left: false, item: task("t1") });
    const second = vi.fn().mockResolvedValue({ left: false, item: task("t2") });
    useTaskStore.getState().schedulePromote({ id: "t1", projectName: "A", commit: first });
    useTaskStore.getState().schedulePromote({ id: "t2", projectName: "B", commit: second });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();
  });
});

// ── capture straight to a project ────────────────────────────────────────────

describe("captureTo", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useTaskStore.setState({
      backend: "live",
      items: [],
      projects: [{ id: "p1", source: "LOCAL", outcome: "Printer v3", status: "ACTIVE", hasNextAction: false }],
      pendingPromote: null,
      promoteNotice: null,
      syncFailure: null,
      personalRootId: "root-1",
    });
    vi.mocked(apiCapture).mockReset();
    vi.mocked(apiMoveTask).mockReset();
    vi.mocked(lensGetItem).mockReset();
    vi.mocked(projectsApi.previewMove).mockReset();
    vi.mocked(fetchAreas).mockResolvedValue([]);
  });
  afterEach(() => {
    vi.useRealTimers();
    useTaskStore.setState({ backend: "demo" });
  });

  it('"Fix the jam #Printer v3" lands on Printer v3, still mine, with "Open board"', async () => {
    vi.mocked(apiCapture).mockResolvedValue(task("new-1", { title: "Fix the jam" }));
    vi.mocked(projectsApi.previewMove).mockResolvedValue({
      destination_project_id: "p1",
      required_missing: [],
    } as never);
    vi.mocked(apiMoveTask).mockResolvedValue({});
    vi.mocked(lensGetItem).mockResolvedValue(
      task("new-1", { title: "Fix the jam", projectId: "p1", disposition: "INBOX" }),
    );

    const parsed = parseProjectToken("Fix the jam #Printer v3", [
      { id: "p1", name: "Printer v3", kind: "project" },
    ]);
    const out = await useTaskStore.getState().captureTo(parsed.title, parsed.match!);

    expect(out).toEqual({});
    expect(apiCapture).toHaveBeenCalledWith("Fix the jam", undefined, undefined);
    // Waiting: nothing sent yet.
    expect(apiMoveTask).not.toHaveBeenCalled();
    expect(useTaskStore.getState().pendingPromote?.projectName).toBe("Printer v3");
    await vi.advanceTimersByTimeAsync(PROMOTE_UNDO_MS);
    await vi.runAllTimersAsync();
    // The owners are left alone: a capture is self-assigned, so it stays mine.
    expect(apiMoveTask).toHaveBeenCalledWith("new-1", { projectId: "p1" });
    expect(useTaskStore.getState().items.find((i) => i.id === "new-1")?.projectId).toBe("p1");
    const notice = useTaskStore.getState().promoteNotice;
    expect(notice && "title" in notice ? notice.title : null).toBe("Moved to Printer v3");
    // The toast carries "Open board" (PromoteToast), for every promote.
    expect(read("components/PromoteToast.tsx")).toMatch(/label: "Open board"/);
  });

  it("a required field it does not carry opens the promote dialog instead of a refusal", async () => {
    vi.mocked(apiCapture).mockResolvedValue(task("new-2"));
    vi.mocked(projectsApi.previewMove).mockResolvedValue({
      destination_project_id: "p1",
      required_missing: ["PO"],
    } as never);
    const out = await useTaskStore
      .getState()
      .captureTo("Order parts", { id: "p1", name: "Printer v3", kind: "project" });
    expect(out).toEqual({ needsFields: { taskId: "new-2", destinationId: "p1" } });
    expect(apiMoveTask).not.toHaveBeenCalled();
    expect(useTaskStore.getState().pendingPromote).toBeNull();
    // The Inbox opens the dialog, prefilled on the destination.
    expect(read("components/InboxView.tsx")).toMatch(
      /setPromote\(\{ id: res\.needsFields\.taskId, destination: res\.needsFields\.destinationId \}\)/,
    );
  });

  it("a refused move leaves a private capture, and the toast names the reason", async () => {
    vi.mocked(apiCapture).mockResolvedValue(task("new-3"));
    vi.mocked(projectsApi.previewMove).mockResolvedValue({
      destination_project_id: "p1",
      required_missing: [],
    } as never);
    vi.mocked(apiMoveTask).mockRejectedValue(new Error("You cannot see that project"));
    await useTaskStore.getState().captureTo("Thing", { id: "p1", name: "Printer v3", kind: "project" });
    await vi.advanceTimersByTimeAsync(PROMOTE_UNDO_MS);
    await vi.runAllTimersAsync();
    const s = useTaskStore.getState();
    expect(s.items.find((i) => i.id === "new-3")?.projectId).toBe("root-1");
    expect(s.syncFailure?.message).toMatch(/Couldn't move it to Printer v3: You cannot see that project\. It stays in your Inbox\./);
  });

  it("an Area is a move inside my tree, sent at once", async () => {
    vi.mocked(apiCapture).mockResolvedValue(task("new-4"));
    vi.mocked(apiMoveTask).mockResolvedValue({});
    vi.mocked(lensGetItem).mockResolvedValue(task("new-4", { projectId: "a1" }));
    await useTaskStore.getState().captureTo("Water plants", { id: "a1", name: "Home", kind: "area" });
    expect(apiMoveTask).toHaveBeenCalledWith("new-4", { projectId: "a1" });
    expect(projectsApi.previewMove).not.toHaveBeenCalled();
    expect(useTaskStore.getState().pendingPromote).toBeNull();
  });
});

// ── Clarify's door waits too ─────────────────────────────────────────────────

describe("deferClarify", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useTaskStore.setState({
      backend: "live",
      items: [task("c1"), task("c2", { createdAt: "2026-09-21T09:00:00Z" })],
      fromProjectIds: new Set(),
      clarifiedThisSession: new Set(),
      processedThisSession: 0,
      selectedItemId: "c1",
      pendingPromote: null,
      undoSnapshot: null,
    });
    vi.mocked(apiOrganize).mockReset();
    vi.mocked(fetchUntriaged).mockResolvedValue([]);
  });
  afterEach(() => {
    vi.useRealTimers();
    useTaskStore.setState({ backend: "demo" });
  });

  const decision = {
    kind: "next" as const,
    nextAction: "Fix it",
    context: "@computer",
    projectId: "p1",
    customFields: { customer_po: "PO-7" },
  };

  it("walks on at once, and sends the organize with the answers after the delay", async () => {
    vi.mocked(apiOrganize).mockResolvedValue(task("c1", { projectId: "p1", disposition: "NEXT" }));
    useTaskStore.getState().deferClarify("c1", decision, undefined, "Printer v3");
    expect(useTaskStore.getState().selectedItemId).toBe("c2");
    expect(apiOrganize).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(PROMOTE_UNDO_MS);
    await vi.runAllTimersAsync();
    expect(apiOrganize).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ project_id: "p1", custom_fields: { customer_po: "PO-7" } }),
    );
    // No Undo snapshot for a sent promote: D62 forbids moving it back.
    expect(useTaskStore.getState().undoSnapshot).toBeNull();
  });

  it("Undo puts the capture back in the walk, and nothing is sent", () => {
    useTaskStore.getState().deferClarify("c1", decision, undefined, "Printer v3");
    expect(useTaskStore.getState().undoPromote()).toBe(true);
    vi.advanceTimersByTime(PROMOTE_UNDO_MS * 2);
    expect(apiOrganize).not.toHaveBeenCalled();
    expect(useTaskStore.getState().clarifiedThisSession.has("c1")).toBe(false);
    expect(useTaskStore.getState().processedThisSession).toBe(0);
  });
});

// ── one promote path ─────────────────────────────────────────────────────────

describe("one promote path (S6g)", () => {
  const dialog = read("components/PromoteDialog.tsx");
  const clarify = read("components/ClarifyPanel.tsx");

  it("both doors build the move through `promotePlan`", () => {
    expect(dialog).toMatch(/const plan = promotePlan\(\{/);
    expect(clarify).toMatch(/const plan = promotePlan\(\{/);
  });

  it("both doors wait through the one deferred commit", () => {
    expect(dialog).toMatch(/schedulePromote\(\{/);
    expect(clarify).toMatch(/deferClarify\(/);
    const store = readFileSync(resolve(__dirname, "taskStore.ts"), "utf-8");
    expect(store).toMatch(/deferClarify: \(id, decision, weight, projectName\) => \{[\s\S]*?get\(\)\.schedulePromote\(\{/);
  });

  it("neither door writes its own toast: the words are one set", () => {
    for (const src of [dialog, clarify]) {
      expect(src).not.toMatch(/Moved to \$\{/);
      expect(src).not.toMatch(/toast\.show\(/);
    }
    // The success toast is built by `promoteToast` for every door, so the two
    // strings are equal by construction.
    const moved = { left: false as const, item: task("x") };
    expect(promoteToast(moved, "Printer v3", null).title).toBe("Moved to Printer v3");
    expect(promotePendingToast("Printer v3").title).toBe("Moving to Printer v3…");
  });

  it("the dialog and the Where hint say one sentence", () => {
    expect(PROMOTE_HINT).toBe(
      "It moves onto the board. It stays in your lists while you are an assignee.",
    );
    expect(read("../projects/components/MoveTasksDialog.tsx")).toMatch(/\$\{PROMOTE_HINT\}/);
    expect(clarify).toMatch(/\? PROMOTE_HINT/);
    // The contradiction is gone.
    expect(read("../projects/components/MoveTasksDialog.tsx")).not.toMatch(/leaves your list/);
  });

  it("Clarify draws the Move dialog's own questions under Where", () => {
    expect(clarify).toMatch(/<PromoteFields[\s\S]*?showAssignees=\{false\}/);
    expect(read("../projects/components/MoveTasksDialog.tsx")).toMatch(/<PromoteFields/);
    // A promote holds the button until every required field is answered.
    expect(clarify).toMatch(/const promoteReady = !promoting \|\| !!promoteState\?\.ready;/);
  });

  it("the idle preview answers constants, so the report effect cannot loop", () => {
    // Found by the S6g visual pass: the Move dialog mounts `PromoteFields`
    // before a destination is picked, and a fresh `[]` per render looped the
    // report effect into "Maximum update depth exceeded".
    const src = read("../projects/components/PromoteFields.tsx");
    expect(src).toMatch(/requiredDefs: IDLE_DEFS, draft: IDLE_DRAFT/);
    expect(src).toMatch(/const IDLE_DEFS: FieldDef\[\] = \[\];/);
  });

  it("Clarify's company list is the dialog's tree, folders disabled", () => {
    expect(clarify).toMatch(/useCompanyTree\(backend === "live"\)/);
    expect(clarify).toMatch(/tree=\{tree\}/);
    expect(dialog).toMatch(/useCompanyTree\(backend === "live", true\)/);
  });
});
