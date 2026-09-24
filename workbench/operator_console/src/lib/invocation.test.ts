// The declare form's verb rule (CP-13b). Mirrors the Console's
// `catalog.check_invocation_for_task`, which refuses a wrong pair with a 400.

import { describe, expect, it } from "vitest";

import {
  NATIVE_PROVIDER,
  verbAfterTaskChange,
  verbFitsModel,
  verbsForTask,
} from "./invocation";

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

// CP-13h review P1-2: the model prefix picks the key, and the verb picks the
// host. Following the AI/ML API guide must never declare `native_typesafe`.
// Mirrors `catalog.check_model_for_invocation`.
describe("the native verb follows the model prefix", () => {
  it("picks native_aimlapi for an aimlapi model on decide", () => {
    expect(verbAfterTaskChange("acompletion", "decide", "aimlapi/typesafe/jev")).toBe(
      "native_aimlapi",
    );
    expect(verbAfterTaskChange("native_typesafe", "decide", "aimlapi/typesafe/jev")).toBe(
      "native_aimlapi",
    );
  });

  it("picks native_typesafe for a typesafe model on decide", () => {
    expect(verbAfterTaskChange("native_aimlapi", "decide", "typesafe/jev-1.13.0")).toBe(
      "native_typesafe",
    );
  });

  it("offers only the matching native verb", () => {
    expect(verbsForTask("decide", "aimlapi/typesafe/jev")).toEqual(["native_aimlapi"]);
    expect(verbsForTask("decide", "typesafe/jev-1.13.0")).toEqual(["native_typesafe"]);
  });

  it("offers every native verb while the model is empty or unknown", () => {
    expect(verbsForTask("decide", "")).toEqual(["native_typesafe", "native_aimlapi"]);
    expect(verbsForTask("decide", "foo/bar")).toEqual(["native_typesafe", "native_aimlapi"]);
  });

  it("refuses the mismatched pairs in both directions", () => {
    expect(verbFitsModel("native_typesafe", "aimlapi/typesafe/jev")).toBe(false);
    expect(verbFitsModel("native_aimlapi", "typesafe/jev-1.13.0")).toBe(false);
    expect(verbFitsModel("native_aimlapi", "aimlapi/typesafe/jev")).toBe(true);
    expect(verbFitsModel("acompletion", "openai/gpt-4o")).toBe(true);
    expect(verbFitsModel("native_constructor", "constructor/x")).toBe(false);
  });

  it("leaves a chat model's verb alone", () => {
    expect(verbAfterTaskChange("acompletion", "chat", "openai/gpt-4o")).toBe("acompletion");
    expect(NATIVE_PROVIDER.native_aimlapi).toBe("aimlapi");
  });
});
