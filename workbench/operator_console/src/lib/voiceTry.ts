// "Try speech to text" and "Try image analysis" — the rules behind the two
// panels on `/tiers` (VOX-4, task 2264).
//
// Spec: `project-docs/specs/projects_ai_chat.md` §31.5 VOX-4 ·
// `project-docs/specs/customer_console.md` §6A.14.
//
// 🔴 **Neither panel calls a customer door.** They call the operator routes
// `POST /catalog/transcribe/try` and `POST /catalog/vision/try`. Each route
// uses the PLATFORM key, writes one audit row and writes NO usage row.
//
// ⚠️ **The Console judges the clip and the image, not this file.** The limits
// below mirror `main.py` (`_TRY_AUDIO_BYTES` and the others) so the button and
// the BFF can refuse before a byte crosses the wire. A Console refusal is
// relayed word for word. A mirror can drift, so `voiceTry.test.ts` reads
// `main.py` and fails when a limit here differs.
//
// ⚠️ Pure on purpose. This app has no React renderer in its suite, so the
// logic sits here and the test file is the fence.

/** The two tiers the routes try. They take no `tier` field. */
export const STT_TIER = "tier-stt";
export const VISION_TIER = "tier-vision";

export const MAX_AUDIO_BYTES = 10 * 1024 * 1024;
export const MAX_AUDIO_SECONDS = 60;
export const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
export const MAX_QUESTION_CHARS = 500;
export const DEFAULT_QUESTION = "Describe this image.";

/** Image types the Console takes. It checks the first bytes, not this list. */
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"] as const;

export type TryKind = "audio" | "image";

/** A refusal made before the Console is called. */
export type Blocker = { status: number; message: string };

const mb = (n: number) => `${Math.round(n / (1024 * 1024))} MB`;

/** Why a chosen file cannot be sent, or null when it can.
 *
 * Only what a person can see: nothing chosen, nothing in the file, or a file
 * over the cap. The Console still measures a WAV clip from its bytes and
 * judges the image type from its first bytes. */
export function fileBlocker(kind: TryKind, file: { size: number } | null | undefined): Blocker | null {
  if (!file) {
    return {
      status: 400,
      message: kind === "audio" ? "Choose a .wav clip first." : "Choose an image first.",
    };
  }
  if (file.size <= 0) return { status: 400, message: "The file has no bytes." };
  const cap = kind === "audio" ? MAX_AUDIO_BYTES : MAX_IMAGE_BYTES;
  if (file.size > cap) {
    return {
      status: 413,
      message:
        kind === "audio"
          ? `A try takes a clip up to ${mb(cap)} and ${MAX_AUDIO_SECONDS} seconds.`
          : `A try takes an image up to ${mb(cap)}.`,
    };
  }
  return null;
}

/** Why the question cannot be sent, or null. A blank question is allowed:
 *  the Console uses its default. */
export function questionBlocker(question: string): Blocker | null {
  if (question.trim().length > MAX_QUESTION_CHARS) {
    return {
      status: 400,
      message: `The question is at most ${MAX_QUESTION_CHARS} characters.`,
    };
  }
  return null;
}

/** The first thing that stops a vision try. */
export function visionBlocker(
  file: { size: number } | null | undefined,
  question: string,
): Blocker | null {
  return fileBlocker("image", file) ?? questionBlocker(question);
}

/** The multipart body of a vision try. */
export function visionForm(file: Blob, question: string): FormData {
  const form = new FormData();
  form.append("file", file, (file as File).name || "image");
  form.append("question", question.trim() || DEFAULT_QUESTION);
  return form;
}

/** The multipart body of a speech try. */
export function audioForm(file: Blob): FormData {
  const form = new FormData();
  form.append("file", file, (file as File).name || "clip.wav");
  return form;
}

export type TranscribeResult = {
  tier: string;
  model: string;
  transcript: string;
  /** The length the Console measured, or the vendor's figure. Null when neither exists. */
  seconds: number | null;
  latencyMs: number;
};

export type VisionResult = {
  tier: string;
  model: string;
  text: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
  vendorCostUsd: string | null;
};

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** The Console's answer to a speech try, or null when it is not that shape. */
export function readTranscribeResult(raw: unknown): TranscribeResult | null {
  if (!isObject(raw)) return null;
  if (typeof raw.model !== "string" || typeof raw.transcript !== "string") return null;
  return {
    tier: typeof raw.tier === "string" ? raw.tier : STT_TIER,
    model: raw.model,
    transcript: raw.transcript,
    seconds: num(raw.seconds),
    latencyMs: num(raw.latency_ms) ?? 0,
  };
}

/** The Console's answer to a vision try, or null when it is not that shape. */
export function readVisionResult(raw: unknown): VisionResult | null {
  if (!isObject(raw)) return null;
  if (typeof raw.model !== "string" || typeof raw.text !== "string") return null;
  const usage = isObject(raw.usage) ? raw.usage : {};
  return {
    tier: typeof raw.tier === "string" ? raw.tier : VISION_TIER,
    model: raw.model,
    text: raw.text,
    inputTokens: num(usage.input_tokens) ?? 0,
    outputTokens: num(usage.output_tokens) ?? 0,
    latencyMs: num(raw.latency_ms) ?? 0,
    vendorCostUsd: typeof raw.vendor_cost_usd === "string" ? raw.vendor_cost_usd : null,
  };
}

/** "3.0 seconds", "1 second", or the reason there is no figure. */
export function secondsText(seconds: number | null): string {
  if (seconds === null) return "not measured (the clip is not a WAV and the vendor sent no length)";
  const rounded = Math.round(seconds * 10) / 10;
  return `${rounded} ${rounded === 1 ? "second" : "seconds"}`;
}

/** The words an operator reads when a try is refused.
 *
 * The Console answers `{"detail": "..."}`. For a vendor failure the detail
 * holds the vendor's own error text (the Console removed any key). A body this
 * page cannot parse is shown as it came, so nothing is hidden. */
export function refusalText(status: number, body: string): string {
  let detail: string | null = null;
  try {
    const parsed: unknown = JSON.parse(body);
    if (isObject(parsed)) {
      if (typeof parsed.detail === "string") detail = parsed.detail;
      else if (typeof parsed.error === "string") detail = parsed.error;
      else if (parsed.detail !== undefined) detail = JSON.stringify(parsed.detail);
    }
  } catch {
    detail = null;
  }
  return `The Console refused (${status}): ${detail ?? (body.trim() || "no reason given")}`;
}

/** A refusal in the Console's own shape, so the page reads it like any other. */
export function blockedResult(b: Blocker): { status: number; body: string } {
  return { status: b.status, body: JSON.stringify({ detail: b.message }) };
}

/** Headroom for the multipart framing around a file. */
const FRAMING_BYTES = 64 * 1024;

/** Refuse from the `Content-Length` header alone, before the body is read.
 *  A missing or unreadable header passes, and the file size check follows. */
export function lengthBlocker(kind: TryKind, header: string | null): Blocker | null {
  const length = header === null ? NaN : Number(header);
  if (!Number.isFinite(length)) return null;
  const cap = (kind === "audio" ? MAX_AUDIO_BYTES : MAX_IMAGE_BYTES) + FRAMING_BYTES;
  if (length <= cap) return null;
  // The size of the file is the header less the framing, which is over the cap.
  return fileBlocker(kind, { size: length - FRAMING_BYTES });
}
