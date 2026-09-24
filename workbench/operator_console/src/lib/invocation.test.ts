// The declare form's verb rule (CP-13b). Mirrors the Console's
// `catalog.check_invocation_for_task`, which refuses a wrong pair with a 400.

import { describe, expect, it } from "vitest";

import { verbAfterTaskChange } from "./invocation";

describe("the verb when the job changes", () => {
  it("moves a chat verb to the native one when the job becomes decide", () => {
    expect(verbAfterTaskChange("acompletion", "decide")).toBe("native_typesafe");
  });

  it("moves the native verb off when the job leaves decide", () => {
    expect(verbAfterTaskChange("native_typesafe", "chat")).toBe("acompletion");
  });

  it("keeps a verb that still fits", () => {
    expect(verbAfterTaskChange("atranscription", "transcribe")).toBe("atranscription");
  });
});
