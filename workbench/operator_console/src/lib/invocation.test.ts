// The declare form's verb rule (CP-13b). Mirrors the Console's
// `catalog.check_invocation_for_task`, which refuses a wrong pair with a 400.

import { describe, expect, it } from "vitest";

import {
  NATIVE_PROVIDER,
  NATIVE_TASK_OF,
  verbAfterTaskChange,
  verbFitsModel,
  verbFitsTask,
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

// VOX-2 (2026-11-08): Sarvam speech to text. `transcribe` takes a litellm
// verb or `native_sarvam`, and a `sarvam/` model on `transcribe` takes
// `native_sarvam` only. Mirrors `catalog.check_invocation_for_task` and
// `catalog.check_model_for_invocation` with the task.
describe("the speech verb follows the job and the model", () => {
  it("offers native_sarvam for transcribe, beside the litellm verbs", () => {
    const offered = verbsForTask("transcribe");
    expect(offered).toContain("native_sarvam");
    expect(offered).toContain("atranscription");
    expect(offered).not.toContain("native_typesafe");
  });

  it("offers only native_sarvam for a sarvam model on transcribe", () => {
    expect(verbsForTask("transcribe", "sarvam/saaras:v3/translit")).toEqual(["native_sarvam"]);
    expect(verbAfterTaskChange("atranscription", "transcribe", "sarvam/saaras:v3")).toBe(
      "native_sarvam",
    );
  });

  it("keeps native_sarvam away from a groq model and from the other jobs", () => {
    expect(verbsForTask("transcribe", "groq/whisper-large-v3-turbo")).not.toContain(
      "native_sarvam",
    );
    expect(verbsForTask("decide")).not.toContain("native_sarvam");
    expect(verbsForTask("chat", "sarvam/sarvam-m")).not.toContain("native_sarvam");
    expect(verbFitsTask("native_sarvam", "transcribe")).toBe(true);
    expect(verbFitsTask("native_sarvam", "decide")).toBe(false);
    expect(verbFitsTask("native_typesafe", "transcribe")).toBe(false);
    expect(verbFitsTask("atranscription", "transcribe")).toBe(true);
  });

  it("maps the verb to its vendor and its job", () => {
    expect(NATIVE_PROVIDER.native_sarvam).toBe("sarvam");
    expect(NATIVE_TASK_OF.native_sarvam).toBe("transcribe");
  });
});

// VOX-6 (2026-11-08): AssemblyAI Sync speech to text. The same rule as Sarvam,
// for an `assemblyai/` model.
describe("the AssemblyAI speech verb", () => {
  const model = "assemblyai/universal-3-5-pro/hi,en";

  it("offers only native_assemblyai for an assemblyai model on transcribe", () => {
    expect(verbsForTask("transcribe", model)).toEqual(["native_assemblyai"]);
    expect(verbAfterTaskChange("atranscription", "transcribe", model)).toBe("native_assemblyai");
  });

  it("offers it beside the litellm verbs when no model is named", () => {
    expect(verbsForTask("transcribe")).toContain("native_assemblyai");
    expect(verbsForTask("decide")).not.toContain("native_assemblyai");
    expect(verbsForTask("transcribe", "sarvam/saaras:v3")).not.toContain("native_assemblyai");
    expect(verbFitsTask("native_assemblyai", "transcribe")).toBe(true);
    expect(verbFitsTask("native_assemblyai", "chat")).toBe(false);
  });

  it("maps the verb to its vendor and its job", () => {
    expect(NATIVE_PROVIDER.native_assemblyai).toBe("assemblyai");
    expect(NATIVE_TASK_OF.native_assemblyai).toBe("transcribe");
  });
});
