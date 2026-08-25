/**
 * D52 fence, client side — the connector surface stays deleted.
 *
 * Board **WS-39 S3a-client slice 4** · decision **D52** · spec
 * `task_manager_app.md` §13.3.
 *
 * The gateway half of this is `tests/unit/test_no_task_provider_connectors.py`,
 * which pins that the provider REGISTRY stays empty. This is the other half and
 * it defends a different failure: the registry can be empty while the browser
 * still ships the whole workspace UI — the account list, the "push to the tool"
 * button, the ClickUp destination picker — every one of which composes a
 * request whose only possible answer is a 400.
 *
 * That is not hypothetical. S1 deleted `ClickUpProvider` and left the sidebar's
 * Connect button, a per-account Sync button and `WorkspacesModal` behind; its
 * repair round had to go back for them. The review's one sentence was
 * **"deleting code does not delete rows or the UI pointing at them"**, and this
 * file is that sentence as a test.
 *
 * Structural, not exemplary: it sweeps the WHOLE `tasks/` and `calendar/` trees
 * rather than naming components, because the failure mode is *one component
 * left behind* and an example test can only see the components somebody
 * remembered to write examples for.
 *
 * ⚠️ Comments are stripped before the sweep. A note explaining what was removed
 * and why is exactly what should survive a deletion — banning the WORD would
 * push the next reader to delete the history along with the code, which is how
 * a decision gets re-litigated in six months by somebody who cannot see it was
 * ever taken.
 *
 * Reversing D52 is an owner decision recorded by name in `work_plan.md` §3. The
 * correct response to this failing is to read that, not to edit this file.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const TASKS = fileURLToPath(new URL("..", import.meta.url));
const CALENDAR = fileURLToPath(new URL("../../calendar", import.meta.url));

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

/** Source with comments removed — see the header. */
function code(file: string): string {
  return readFileSync(file, "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1")
    // JSX comments are `{/* … */}`; the block rule above already took the
    // inner part, leaving `{}` behind, which is harmless.
    .replace(/\{\s*\}/g, "");
}

const FILES = [...walk(TASKS), ...walk(CALENDAR)];

describe("the connector surface is gone from the client (D52)", () => {
  it("sweeps a real tree", () => {
    // Blind-walk guard. A sweep that finds no files passes every assertion
    // below and proves nothing — the failure mode the gateway fence's own
    // header calls out.
    expect(FILES.length).toBeGreaterThan(40);
    expect(FILES.some((f) => f.endsWith("taskStore.ts"))).toBe(true);
    expect(FILES.some((f) => f.endsWith("ClarifyPanel.tsx"))).toBe(true);
  });

  /**
   * Names that existed only to talk to a connected workspace. Each is paired
   * with what it did, because "why was this banned" is the question somebody
   * will have when it fails.
   */
  const BANNED: Readonly<Record<string, string>> = {
    TaskAccount: "the connected-workspace row; there are no workspaces",
    accountToProviderEntry: "mapped an account into the destination picker",
    fetchAccounts: "GET /tasks/accounts",
    apiSyncTasks: "POST /tasks/sync — builds a provider, so it can only 400",
    apiPushItem: "POST /tasks/items/{id}/push — nothing to push to",
    apiRefreshSchema: "re-pulled the workspace's space/folder/list shape",
    apiRefreshMembers: "re-pulled the workspace's member roster",
    apiCreateAccountProject: "created a ClickUp list",
    apiCreateAccountFolder: "created a ClickUp folder",
    providerStatusesFrom: "unioned every workspace's statuses",
    delegateLocalToClickUp: "promoted a local task into a workspace",
    createWorkspaceProject: "store action for the ClickUp list write",
    createWorkspaceFolder: "store action for the ClickUp folder write",
    refreshAccountMembers: "store action for the member pull",
    refreshAccountSchema: "store action for the schema pull",
    destAccount: "the Clarify destination's workspace",
    apiCalendarRange: "GET /tasks/calendar — had no callers at all",
  };

  it("names none of the retired symbols", () => {
    const hits: string[] = [];
    for (const file of FILES) {
      const src = code(file);
      for (const [name, what] of Object.entries(BANNED)) {
        if (new RegExp(`\\b${name}\\b`).test(src)) {
          hits.push(`${file.split(/[\\/]/).slice(-2).join("/")} → ${name} (${what})`);
        }
      }
    }
    expect(
      hits,
      "the client still names a retired connector symbol:\n  " +
        hits.join("\n  ") +
        "\nD52 removed the connector; a UI that still composes its requests " +
        "gets a 400 and shows the user a control that cannot succeed.",
    ).toEqual([]);
  });

  it("addresses no connector endpoint", () => {
    // ⚠️ `/spaces`, `/folders`, `/local-projects` and `/hierarchy` are NOT
    // here, and that is the correction this slice made to its own handoff
    // note. They are the LOCAL Space→Folder→Project tree
    // (`routes/tasks/hierarchy.py`), not a connector surface — they write
    // `gtd_projects` and their destination under D53 is `pm_projects`. Banning
    // them would have deleted the Tasks app's ability to organise projects at
    // all, on the strength of a list that grouped them by the wrong thing.
    const hits: string[] = [];
    for (const file of FILES) {
      const src = code(file);
      for (const path of ["`/accounts", "`/sync", "/push`"]) {
        if (src.includes(path)) hits.push(`${file} → ${path}`);
      }
    }
    expect(hits, `connector endpoints still addressed: ${hits.join(", ")}`)
      .toEqual([]);
  });

  it("keeps no always-empty account state on the store", () => {
    const store = code(join(TASKS, "lib", "taskStore.ts"));
    for (const field of ["accounts:", "providerStatuses:", "syncing:"]) {
      expect(
        store.includes(field),
        `the task store still declares \`${field}\` — it can only ever hold ` +
          "the empty value, and an always-empty field is one the next reader " +
          "spends an afternoon proving is empty",
      ).toBe(false);
    }
  });
});
