import { describe, expect, it } from "vitest";

import type { TaskTypeRow } from "./api";
import {
  SUBTASK_EPIC_REASON,
  epicTypeOf,
  isEpicTask,
  makeEpicTarget,
  typeRefusal,
  typeSelectOptions,
} from "./taskEpic";

const bug: TaskTypeRow = { id: "t-bug", name: "Bug" };
const epic: TaskTypeRow = { id: "t-epic", name: "Epic", is_epic: true };
const types = [bug, epic];

describe("epic type rules", () => {
  it("finds the epic type, or none", () => {
    expect(epicTypeOf(types)?.id).toBe("t-epic");
    expect(epicTypeOf([bug])).toBeUndefined();
    expect(epicTypeOf(undefined)).toBeUndefined();
  });

  it("offers Make it an epic only when it would change something", () => {
    expect(makeEpicTarget({ type_id: null }, types)?.id).toBe("t-epic");
    expect(makeEpicTarget({ type_id: "t-bug" }, types)?.id).toBe("t-epic");
    expect(makeEpicTarget({ type_id: "t-epic" }, types)).toBeUndefined();
    expect(makeEpicTarget({ type_id: null }, [bug])).toBeUndefined();
    expect(isEpicTask({ type_id: "t-epic" }, types)).toBe(true);
  });

  it("refuses an epic type on a subtask, with the reason", () => {
    expect(typeRefusal({ parent_task_id: "p1" }, epic)).toBe(SUBTASK_EPIC_REASON);
    expect(typeRefusal({ parent_task_id: null }, epic)).toBeNull();
    expect(typeRefusal({ parent_task_id: "p1" }, bug)).toBeNull();
    expect(typeRefusal({ parent_task_id: "p1" }, undefined)).toBeNull();
  });

  it("lists No type first, then every type", () => {
    expect(typeSelectOptions(types).map((o) => o.value)).toEqual(["", "t-bug", "t-epic"]);
  });

  it("disables the epic option on a subtask and says why", () => {
    const opts = typeSelectOptions(types, { parent_task_id: "p1" });
    expect(opts.find((o) => o.value === "t-epic")?.disabled).toBe(true);
    expect(opts.find((o) => o.value === "t-epic")?.description).toBe(SUBTASK_EPIC_REASON);
    expect(opts.find((o) => o.value === "t-bug")?.disabled).toBe(false);
  });
});
