import { expect, test, type Page } from "@playwright/test";

/**
 * The edit card of a new Projects task (`projects_ai_chat.md` §32, slice EAC-2,
 * task 3469). In a real browser, on the stubbed chat harness of
 * `email-redraft.spec.ts`: no model and no server run. The stub sends ONE
 * `create_task` confirmation card with the `edit` form that EAC-1 adds when
 * `PROJECT_EDIT_CARD_ORGS` is on, and it records each answer of
 * `/api/agent/respond-input`. The directory is a stub too.
 *
 * The owner can preview the card with this file: `npx playwright test
 * e2e/task-edit-card.spec.ts --project=chromium --headed`. A live `dev_up`
 * stack cannot draw it, because Mira chat has no model there.
 */

const REQ = "a".repeat(32);
const PID = "11111111-2222-4333-8444-555555555555";
const ANN = "ann@acme.test";
const BOB = "bob@acme.test";
const CAT = "cat@acme.test";
const DESCRIPTION = "Ship the pack.\nThen call Kim.";
const VALUES = {
  due: "2026-12-01", start: "", status: "Doing", important: null, leveraged: false,
  description: DESCRIPTION, assignees: [ANN, BOB],
};
const DIRECTORY = [
  { assignee: ANN, name: "Ann Rao" },
  { assignee: BOB, name: "Bob Lee" },
  { assignee: CAT, name: "Cat Jones" },
];
const CONTEXT = [
  "Check each field, then approve.",
  "project: «Launch»",
  "name: «Ship the pack»",
  "due_at: 2026-12-01",
  "status: Doing",
  `assignees: ${ANN}, ${BOB}`,
].join("\n");

declare global {
  interface Window { __answers: { request_id: string; answer: string }[] }
}

async function install(page: Page, withEdit = true) {
  await page.addInitScript(({ withEdit, values, pid, req, context }) => {
    window.localStorage.clear();
    const w = window as unknown as Window;
    w.__answers = [];
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/agent/cancel")) return json({ cancelled: true });
      if (url.includes("/api/agent/respond-input")) {
        w.__answers.push(JSON.parse(String(init?.body ?? "{}")));
        return json({ ok: true });
      }
      if (url.includes("/api/chat/active-sessions")) return json([]);
      if (url.includes("/api/auth/me")) {
        return json({
          email: "jane@acme.test", user_id: "jane", authenticated: true, is_active: true,
          organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
          roles: ["owner"], legacy_role: "executive", features: ["chat", "projects"],
          features_denied: [], agents: ["orchestrator"], permissions: ["*"],
          capabilities: [], denied: [], is_admin: true,
        });
      }
      if (url.includes("/api/health")) return json({ gateway: "up", build: "e2e" });
      if (url.includes("/api/chat/sessions")) {
        if (url.includes("/room")) return json({ detail: "not a room" }, 404);
        return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
      }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) return json({}, 404);
        const stream = new ReadableStream<Uint8Array>({
          start(ctl) {
            ctl.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            ctl.enqueue(frame({ type: "delta", messageId: "m-1", content: "Here is the task.\n\n" }));
            ctl.enqueue(frame({ type: "tool_start", id: "t-1", name: "create_task", args: {} }));
            ctl.enqueue(frame({ type: "custom", name: "confirmation_requested", value: {
              title: "Create this task?", detail: "«Ship the pack» · in Launch · status Doing",
              context, fenced: true, request_id: req,
              ...(withEdit ? { edit: { kind: "create_task", project_id: pid, values } } : {}),
            } }));
            // The run waits on the card. The stream stays open.
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { withEdit, values: VALUES, pid: PID, req: REQ, context: CONTEXT });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
  // The directory: the picker's list, and the exact lookup of the names. Bob is a known address, `typo` is not.
  await page.route("**/api/projects/assignees**", (r) => {
    const q = (new URL(r.request().url()).searchParams.get("q") ?? "").toLowerCase();
    const rows = DIRECTORY.filter((p) => !q || p.name.toLowerCase().includes(q) || p.assignee.includes(q))
      .map((p) => ({ ...p, kind: "person", has_login: true, top_skills: [], warnings: [] }));
    return r.fulfill({ json: { people: rows, agents: [], hr_visible: false } });
  });
  await page.route("**/api/projects/people/names**", (r) => {
    const emails = (new URL(r.request().url()).searchParams.get("emails") ?? "").split(",").filter(Boolean);
    const names: Record<string, string> = {};
    for (const p of DIRECTORY) if (emails.includes(p.assignee)) names[p.assignee] = p.name;
    return r.fulfill({ json: { names } });
  });
}

async function openCard(page: Page, withEdit = true) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await install(page, withEdit);
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Make a task to ship the pack.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator("[data-confirmation-card]")).toBeVisible({ timeout: 15_000 });
}

const answers = (page: Page) => page.evaluate(() => (window as unknown as Window).__answers);
const approve = async (page: Page) => {
  // The card arms again after a mount (`ARM_MS`).
  await page.waitForTimeout(600);
  await page.locator("[data-confirmation-approve]").click();
  await expect.poll(async () => (await answers(page)).length).toBe(1);
  return (await answers(page))[0];
};

test.describe("the edit card of a new task", () => {
  test.describe.configure({ timeout: 120_000 });

  test("edit the due date, take one person off and add another, then Approve: the answer holds the seven keys", async ({ page }) => {
    await openCard(page);
    // The form: a date field, a chip for each person, and the rows that it replaces are gone.
    await expect(page.locator("[data-task-edit-card]")).toBeVisible();
    await expect(page.locator("[data-task-edit-due]")).toHaveValue("2026-12-01");
    await expect(page.locator('[data-card-field="due_at"]')).toHaveCount(0);
    await expect(page.locator('[data-card-field="status"]')).toBeVisible();

    await page.locator("[data-task-edit-due]").fill("2027-03-15");
    await page.getByRole("button", { name: `Unassign ${ANN}` }).click();
    // A name: it must match one person exactly. Enter adds, and Enter does not approve.
    const person = page.getByRole("textbox", { name: "Add a person" });
    await person.fill("Cat Jones");
    await person.press("Enter");
    await expect(page.getByRole("button", { name: `Unassign ${CAT}` })).toBeVisible();
    expect(await answers(page)).toHaveLength(0);
    await expect(page.locator("[data-task-edit-card]")).toHaveAttribute("data-task-edit-state", "edited");
    await expect(page.locator("[data-task-edit-edited]")).toContainText(`added ${CAT} · removed ${ANN}`);

    const sent = await approve(page);
    expect(sent.request_id).toBe(REQ);
    expect(sent.answer).toBe(`APPROVE ${JSON.stringify({ edit: {
      due: "2027-03-15", start: "", status: "Doing", important: null, leveraged: false,
      description: DESCRIPTION, assignees: [BOB, CAT],
    } })}`);
    // No id leaves the browser.
    expect(sent.answer).not.toContain(PID);
  });

  test("a card that nobody changed sends the plain APPROVE", async ({ page }) => {
    await openCard(page);
    const sent = await approve(page);
    expect(sent.answer).toBe("APPROVE");
  });

  test("an address that the directory does not know is named before Approve, and a name that is not whole is refused", async ({ page }) => {
    await openCard(page);
    const person = page.getByRole("textbox", { name: "Add a person" });
    // A part of a name matches nobody on the server, so the form says so before the Approve.
    await person.fill("Cat");
    await person.press("Enter");
    await expect(page.locator("[data-task-edit-line]")).toContainText("Nobody is called exactly “Cat”");
    await expect(page.getByRole("button", { name: `Unassign ${CAT}` })).toHaveCount(0);
    // Text that no Enter added holds Approve, with the reason beside it. Escape clears it.
    await expect(page.locator("[data-confirmation-hint]")).toContainText("Press Enter to add “Cat”");
    await expect(page.locator("[data-confirmation-approve]")).toBeDisabled();
    await person.press("Escape");
    await expect(person).toHaveValue("");
    await expect(page.locator("[data-confirmation-approve]")).toBeEnabled();
    // An address passes as typed, and the lookup says the directory does not know it.
    await person.fill("typo@acme.tset");
    await person.press("Enter");
    const stranger = page.locator("[data-task-edit-stranger]");
    await expect(stranger).toContainText("Not in the directory: typo@acme.tset");
    await expect(stranger).not.toContainText(BOB);
    expect(await answers(page)).toHaveLength(0);
    // The warning does not stop the member: the address is still the member's choice (H-162).
    await expect(page.locator("[data-confirmation-approve]")).toBeEnabled();
    const sent = await approve(page);
    expect(JSON.parse(sent.answer.slice("APPROVE ".length)).edit.assignees).toEqual([ANN, BOB, "typo@acme.tset"]);
  });

  test("Back to the card puts each original value back, and the answer is the plain APPROVE again", async ({ page }) => {
    await openCard(page);
    await page.locator("[data-task-edit-due]").fill("2027-03-15");
    await page.getByRole("button", { name: `Unassign ${BOB}` }).click();
    await page.locator("[data-task-edit-reset]").click();
    await expect(page.locator("[data-task-edit-due]")).toHaveValue("2026-12-01");
    await expect(page.getByRole("button", { name: `Unassign ${BOB}` })).toBeVisible();
    await expect(page.locator("[data-task-edit-card]")).toHaveAttribute("data-task-edit-state", "original");
    expect((await approve(page)).answer).toBe("APPROVE");
  });

  test("a cleared date sends an empty due", async ({ page }) => {
    await openCard(page);
    await page.locator("[data-task-edit-clear-due]").click();
    await expect(page.locator("[data-task-edit-due]")).toHaveValue("");
    const sent = await approve(page);
    expect(JSON.parse(sent.answer.slice("APPROVE ".length)).edit.due).toBe("");
  });

  test("a change of chat keeps the edits, and Approve sends them (task 1893)", async ({ page }) => {
    await openCard(page);
    await page.locator("[data-task-edit-due]").fill("2027-03-15");
    await page.getByRole("button", { name: `Unassign ${ANN}` }).click();
    // Another chat, then back to this one.
    await page.getByRole("button", { name: "+ New conversation" }).click();
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    await expect(page.locator("[data-confirmation-card]")).toHaveCount(0, { timeout: 10_000 });
    await page.getByRole("img", { name: "Assistant is working" }).first().click();
    await expect(page.locator("[data-task-edit-card]")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("[data-task-edit-due]")).toHaveValue("2027-03-15");
    await expect(page.getByRole("button", { name: `Unassign ${ANN}` })).toHaveCount(0);
    await expect(page.getByRole("button", { name: `Unassign ${BOB}` })).toBeVisible();
    const sent = await approve(page);
    const edit = JSON.parse(sent.answer.slice("APPROVE ".length)).edit;
    expect(edit.due).toBe("2027-03-15");
    expect(edit.assignees).toEqual([BOB]);
  });

  test("with no `edit` on the event the card is the card of before", async ({ page }) => {
    await openCard(page, false);
    await expect(page.locator("[data-task-edit-card]")).toHaveCount(0);
    await expect(page.locator('[data-card-field="due_at"]')).toBeVisible();
    expect((await approve(page)).answer).toBe("APPROVE");
  });

  test("Reject sends REJECT and no value", async ({ page }) => {
    await openCard(page);
    await page.locator("[data-task-edit-due]").fill("2027-03-15");
    await page.waitForTimeout(600);
    await page.getByRole("button", { name: "Reject" }).click();
    // The REJECT waits five seconds for Undo (`lib/rejectHold.ts`).
    await expect.poll(async () => (await answers(page)).length, { timeout: 15_000 }).toBe(1);
    expect((await answers(page))[0].answer).toBe("REJECT");
  });
});
