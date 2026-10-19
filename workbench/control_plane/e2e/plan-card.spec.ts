import { expect, test, type Page } from "@playwright/test";

import { PLAN_TASKS, installPlanScenario, respondBodies, startPlanTurn } from "./plan-card-fixture";

/**
 * The plan card, read first (owner, 2026-10-18).
 *
 * The owner said the old card "feels intimidating and the UI looks poor",
 * and approved nine changes. The node suite holds the pure decisions and the
 * markup at rest. This spec holds what only a browser can show: a row opens
 * on a click and on Enter, focus comes back, the ticks count, the bulk edits
 * and the fold work, and the answer the card sends is the old card's shape.
 *
 * The chat page is real. The stream is held open in the page, so the card
 * waits as a parked run does (`plan-card-fixture.ts`).
 */

const card = (page: Page) => page.locator("[data-plan-card]").first();
const line = (page: Page, title: string) =>
  card(page).getByRole("button", { name: new RegExp(`^(Edit|Pick) ${title}:`) });

/** The JSON half of the answer the card sent: "<label> — <json>". */
async function sentPlan(page: Page): Promise<{ label: string; plan: Record<string, unknown> }> {
  await expect.poll(async () => (await respondBodies(page)).length).toBeGreaterThan(0);
  const answer = String((await respondBodies(page)).at(-1)?.answer ?? "");
  const [label, json] = answer.split(" — ");
  return { label, plan: JSON.parse(json) };
}

test.describe("The plan card reads first", () => {
  test.describe.configure({ timeout: 120_000 });

  test("a click opens one row, Enter opens one, and Escape gives focus back", async ({ page }) => {
    await installPlanScenario(page);
    await startPlanTurn(page);
    // At rest: no text field in the card.
    await expect(card(page).getByRole("textbox")).toHaveCount(0);

    await line(page, "Design the recall API").click();
    await expect(card(page).locator("[data-plan-open]")).toHaveCount(1);
    await expect(card(page).getByRole("textbox", { name: "Task" })).toHaveValue("Design the recall API");

    // Open another: the first one closes, so one row is open at a time.
    await line(page, "Load test the retrieval path").click();
    await expect(card(page).locator("[data-plan-open]")).toHaveCount(1);
    await expect(card(page).getByRole("textbox", { name: "Task" })).toHaveValue("Load test the retrieval path");
    // Its read-only detail: the fit, the hours and the full mark.
    await expect(card(page).getByText("Fit: no skill matched")).toBeVisible();
    await expect(card(page).getByText(/Load testing is not on the owner's skills/)).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(card(page).locator("[data-plan-open]")).toHaveCount(0);
    await expect(line(page, "Load test the retrieval path")).toBeFocused();

    // The keyboard: Enter on a focused line opens it.
    await line(page, "Build the memory store and write path").focus();
    await page.keyboard.press("Enter");
    await expect(card(page).getByRole("textbox", { name: "Task" })).toHaveValue("Build the memory store and write path");
    // The priority score shows in the open row, with its tip.
    await expect(card(page).locator("[data-plan-priority]")).toHaveText("Priority 24 · a sorting aid");
    await card(page).getByRole("button", { name: "Done", exact: true }).click();
    await expect(line(page, "Build the memory store and write path")).toBeFocused();
    await expect(card(page).getByText("Priority 24")).toHaveCount(0);
  });

  test("the ticks count in the footer, and the answer keeps the old shape", async ({ page }) => {
    await installPlanScenario(page);
    await startPlanTurn(page);
    const submit = card(page).locator("[data-plan-submit]");
    await expect(submit).toHaveText("Create 8 tasks");
    await card(page).getByRole("checkbox", { name: "Create Write the privacy and deletion controls" }).uncheck();
    await expect(submit).toHaveText("Create 7 tasks");
    await card(page).getByRole("checkbox", { name: "Create Design the recall API" }).uncheck();
    await expect(submit).toHaveText("Create 6 tasks");
    await card(page).getByRole("checkbox", { name: "Create Design the recall API" }).check();
    await expect(submit).toHaveText("Create 7 tasks");
    await expect(card(page).getByText("Next, one approval creates all 7 tasks.", { exact: false })).toBeVisible();

    // Edit one effort in days: the answer carries minutes.
    await line(page, "Ship behind a flag and review the metrics").click();
    await card(page).getByRole("button", { name: "Effort unit" }).click();
    await page.getByRole("option", { name: "days" }).click();
    await card(page).getByRole("spinbutton", { name: "Effort" }).fill("1.5");
    await card(page).getByRole("button", { name: "Done", exact: true }).click();
    await expect(line(page, "Ship behind a flag and review the metrics")).toContainText("1.5d");

    await submit.click();
    const { label, plan } = await sentPlan(page);
    expect(label).toBe("Review plan");
    const tasks = plan.tasks as Array<Record<string, unknown>>;
    expect(tasks.map((t) => t.key)).toEqual(["t1", "t2", "t3", "t4", "t5", "t6", "t8"]);
    expect(tasks.find((t) => t.key === "t8")).toMatchObject({ effort_mins: 720, after: ["t6"] });
    // The keys and their order are exactly the old card's (planCard.ts planSubmit).
    expect(Object.keys(tasks[0])).toEqual([
      "key", "title", "owner", "effort_mins", "start", "due", "after",
      "important", "leveraged", "impact", "urgency", "effort",
    ]);
    expect(plan.project).toEqual({ name: "Personalization & Memory System", parent: "Metorite", description: "" });
    await expect(card(page).getByText("Sent", { exact: true })).toBeVisible();
  });

  test("Edit several sets one owner and moves dates together", async ({ page }) => {
    await installPlanScenario(page);
    await startPlanTurn(page);
    await card(page).getByRole("button", { name: "Edit several" }).click();
    await line(page, "Design the recall API").click();
    await line(page, "Write the privacy and deletion controls").click();
    await expect(card(page).getByText("2 picked")).toBeVisible();
    await card(page).getByLabel("Owner for the picked tasks").fill("sam@fracktal.in");
    await card(page).getByRole("button", { name: "Set owner" }).click();
    await card(page).getByRole("spinbutton", { name: "Days to move the picked tasks by" }).fill("2");
    await card(page).getByRole("button", { name: "Move dates" }).click();
    await card(page).getByRole("button", { name: "Done", exact: true }).click();
    await expect(line(page, "Design the recall API")).toContainText("Sam");
    await expect(line(page, "Design the recall API")).toContainText("25 Oct");

    await card(page).locator("[data-plan-submit]").click();
    const tasks = (await sentPlan(page)).plan.tasks as Array<Record<string, unknown>>;
    expect(tasks.find((t) => t.key === "t2")).toMatchObject({ owner: "sam@fracktal.in", start: "2026-10-25", due: "2026-10-25" });
    expect(tasks.find((t) => t.key === "t7")).toMatchObject({ owner: "sam@fracktal.in", start: "2026-11-07", due: "2026-11-09" });
    expect(tasks.find((t) => t.key === "t1")).toMatchObject({ owner: "ayush@fracktal.in", start: "2026-10-22" });
  });

  test("a phase folds away, and Cancel sends no submit", async ({ page }) => {
    await installPlanScenario(page);
    await startPlanTurn(page);
    await expect(line(page, "Load test the retrieval path")).toBeVisible();
    await card(page).getByRole("button", { name: /^Rollout/ }).click();
    await expect(line(page, "Load test the retrieval path")).toHaveCount(0);
    await expect(line(page, "Design the recall API")).toBeVisible();

    await card(page).getByRole("button", { name: "Cancel", exact: true }).click();
    // A plan card that parks the run sends Cancel as a decline (PR #1152), so
    // the run reads "the member cancelled the plan" and nothing is created.
    await expect.poll(async () => (await respondBodies(page)).at(-1)).toMatchObject({ declined: true, answer: "" });
    await expect(card(page).getByText("Nothing was created.")).toBeVisible();
  });

  test("the name renames in the summary line, and Escape reverts it", async ({ page }) => {
    await installPlanScenario(page);
    await startPlanTurn(page);
    // Escape puts the old name back.
    await card(page).locator("[data-plan-name]").click();
    await card(page).getByRole("textbox", { name: "Project name" }).fill("Scrap this");
    await page.keyboard.press("Escape");
    await expect(card(page).locator("[data-plan-name]")).toContainText("Personalization & Memory System");
    await card(page).locator("[data-plan-name]").click();
    const name = card(page).getByRole("textbox", { name: "Project name" });
    await name.fill("Memory v1");
    await page.keyboard.press("Enter");
    await expect(card(page).locator("[data-plan-name]")).toBeFocused();
    await card(page).locator("[data-plan-submit]").click();
    expect((await sentPlan(page)).plan.project).toMatchObject({ name: "Memory v1" });
  });

  test("at 390 px each task is a small stacked card, and the page does not scroll sideways", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installPlanScenario(page);
    await startPlanTurn(page);
    const row = card(page).locator('[data-plan-row="t1"]');
    await expect(row).toBeVisible();
    // Stacked: the owner chip sits under the title, not beside it.
    const title = await row.getByText(PLAN_TASKS[0].title).boundingBox();
    const chip = await row.getByText("Ayush").boundingBox();
    expect(chip!.y).toBeGreaterThan(title!.y + title!.height - 2);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(sideways).toBeLessThanOrEqual(0);
  });
});
