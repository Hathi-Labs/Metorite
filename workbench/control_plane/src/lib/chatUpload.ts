/**
 * The rules of a chat upload, in one place (the incident of 2026-10-09).
 *
 * `FileUploadButton` and `AgentChat` read this file. It holds:
 *
 * - `CHAT_UPLOAD_ACCEPT`: the kinds the picker offers. Each is a kind that
 *   `read_attachment` reads (`acb_skills.attachment_text.SUPPORTED_SUFFIXES`)
 *   or an image type that the gateway takes (`routes/workspace.py`
 *   `_ALLOWED_EXTENSIONS`).
 * - `CHAT_UPLOAD_MAX_BYTES`: the gateway's cap of one file, so a big file is
 *   refused here before it crosses the network.
 * - `uploadErrorMessage`: the sentence a member reads when the gateway
 *   refuses an upload. It is the gateway's `detail`, never a red icon alone.
 * - `uploadNote`: the message that tells the agent what was attached. It
 *   names `read_attachment`, the one tool that reads a chat upload.
 *
 * Fence: `src/lib/chatUpload.test.ts`, which also reads the two Python lists.
 */

/** The kinds that `read_attachment` reads. Keep in step with the Python set. */
export const READABLE_UPLOAD_KINDS: readonly string[] = [
  ".docx", ".xlsx", ".pdf", ".html", ".htm", ".txt", ".md", ".csv",
];

/** The image types that the gateway's upload route takes. */
export const IMAGE_UPLOAD_KINDS: readonly string[] = [
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
];

/** The kinds the chat's file picker offers, in one list. */
export const CHAT_UPLOAD_ACCEPT: readonly string[] = [
  ...READABLE_UPLOAD_KINDS,
  ...IMAGE_UPLOAD_KINDS,
];

/** The `accept` attribute of the chat's file input. */
export const CHAT_UPLOAD_ACCEPT_ATTR = CHAT_UPLOAD_ACCEPT.join(",");

/** The gateway's cap of one file (`_MAX_UPLOAD_BYTES`, 25 MB). */
export const CHAT_UPLOAD_MAX_BYTES = 25 * 1024 * 1024;

function kindOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

/**
 * Why these files cannot be sent, or `null` when every file may go.
 *
 * A drop on the drop zone skips the picker's `accept` list, so the kind is
 * checked here too.
 */
export function refuseUpload(files: ReadonlyArray<{ name: string; size: number }>): string | null {
  for (const f of files) {
    if (f.size > CHAT_UPLOAD_MAX_BYTES) {
      return `${f.name} is larger than 25 MB. Attach a smaller file.`;
    }
    if (!CHAT_UPLOAD_ACCEPT.includes(kindOf(f.name))) {
      return (
        `${f.name} is a kind the assistant cannot read. ` +
        `Attach one of: ${CHAT_UPLOAD_ACCEPT.join(" ")}.`
      );
    }
  }
  return null;
}

/**
 * How long an upload waits before its one retry of a 404. Each chat surface
 * saves its session row in the background when the chat opens
 * (`sessions.upsertSession`). A member who attaches at once can reach the
 * gateway first, and the gateway answers 404 "No workspace found". One retry
 * lets the row land, as `postMessagesWithRetry` does for a save.
 */
export const UPLOAD_SESSION_RETRY_MS = 1_000;

/**
 * POST *form* to the session's upload route, and retry ONE 404 after
 * `delayMs`. Every other answer is final. The form is sent as it is both
 * times.
 */
export async function postUpload(
  sessionId: string,
  form: FormData,
  delayMs: number = UPLOAD_SESSION_RETRY_MS,
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const send = () =>
    fetchImpl(`/api/agent/workspace/${sessionId}/upload`, { method: "POST", body: form });
  const first = await send();
  if (first.status !== 404) return first;
  await new Promise((resolve) => setTimeout(resolve, delayMs));
  return send();
}

/** The gateway's own sentence from a body, or `null`. */
function detailOf(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const rec = body as Record<string, unknown>;
  if (typeof rec.detail === "string" && rec.detail.trim()) return rec.detail.trim();
  // The proxy wraps the gateway's text as `{ error: "<the gateway body>" }`.
  if (typeof rec.error === "string" && rec.error.trim()) {
    try {
      const inner = detailOf(JSON.parse(rec.error));
      if (inner) return inner;
    } catch {
      /* not JSON: a plain sentence */
    }
    return rec.error.trim().length <= 300 ? rec.error.trim() : null;
  }
  return null;
}

/**
 * The sentence a member reads when an upload fails. A 400, 404 or 413 carries
 * the gateway's `detail`, which says what to change. Any other failure gets a
 * plain sentence with its status.
 */
export function uploadErrorMessage(status: number, body: unknown): string {
  if (status === 400 || status === 404 || status === 413) {
    const detail = detailOf(body);
    if (detail) return detail;
  }
  if (status === 413) return "The file is too large. Attach a file of 25 MB or less.";
  return `The upload failed (HTTP ${status}). Try again.`;
}

/**
 * The note that tells the agent what the member attached. It names
 * `read_attachment` and no list of kinds: the tool says which kinds it
 * reads, so a new kind needs no edit here.
 */
export function uploadNote(files: ReadonlyArray<{ name: string; path: string }>): string {
  const names = files.map((f) => f.name).join(", ");
  const paths = files.map((f) => `\`${f.path}\``).join(", ");
  return (
    `📎 Uploaded ${files.length} file(s): ${names}\n\n` +
    `Paths: ${paths}\n\n` +
    "Read them with read_attachment."
  );
}
