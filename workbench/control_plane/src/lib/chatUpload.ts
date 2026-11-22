/**
 * The rules of a chat upload, in one place (the incident of 2026-10-09).
 *
 * `FileUploadButton` and `AgentChat` read this file. It holds:
 *
 * - `CHAT_UPLOAD_ACCEPT`: the kinds the gateway's upload route takes
 *   (`routes/workspace.py` `_ALLOWED_EXTENSIONS`). The client refuses only
 *   what the gateway refuses. A kind that `read_attachment` cannot read
 *   still uploads, and the tool then says which kinds it reads.
 * - `CHAT_UPLOAD_MAX_BYTES`: the gateway's cap of one file, so a big file is
 *   refused here before it crosses the network.
 * - `uploadErrorMessage`: the sentence a member reads when the gateway
 *   refuses an upload. It is the gateway's `detail`, never a red icon alone.
 * - `uploadNote`: the message that tells the agent what was attached. It
 *   names `read_attachment`, the one tool that reads a chat upload.
 *
 * Fence: `src/lib/chatUpload.test.ts`, which compares this list with the
 * gateway's.
 */

/** The kinds the gateway's upload route takes, in its order. */
export const CHAT_UPLOAD_ACCEPT: readonly string[] = [
  ".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".tsv",
  ".odt", ".ods", ".odp", ".rtf",
  ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".js", ".ts",
  ".py", ".sh", ".ps1", ".toml", ".ini", ".cfg",
  ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
  ".mp3", ".wav", ".mp4", ".webm",
  ".zip", ".tar", ".gz", ".bz2", ".7z",
  ".log", ".sql", ".db", ".sqlite",
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
        `${f.name} is a kind that a chat does not take. ` +
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

/** A file that the note names: its name, and its path in the thread folder. */
export interface NotedFile {
  name: string;
  path: string;
}

/** The words of a message, and the files that its trailing note names. */
export interface ParsedUploadNote {
  words: string;
  files: NotedFile[];
}

const NOTE_RE =
  /^(?:([\s\S]*?)\n\n)?📎 Uploaded (\d+) file\(s\): ([^\n]+)\n\nPaths: (`[^`\n]+`(?:, `[^`\n]+`)*)\n\nRead them with read_attachment\.$/;

/**
 * The ONE parser of `uploadNote`. It reads a message and finds the note at the
 * END. It returns the words before the note and the files that it names. It
 * returns `null` when the message holds no whole note, so a changed or
 * partial note stays plain text. The bubble, the thread preview and the copy
 * action all read this and never a regex of their own.
 */
export function parseUploadNote(content: string): ParsedUploadNote | null {
  const m = NOTE_RE.exec(content);
  if (!m) return null;
  const paths = Array.from(m[4].matchAll(/`([^`\n]+)`/g), (p) => p[1]);
  if (paths.length !== Number(m[2]) || paths.length === 0) return null;
  const files = paths.map((path) => ({ path, name: path.slice(path.lastIndexOf("/") + 1) }));
  // The header lists the names. They must equal the path endings, or the
  // note was changed and stays text.
  if (files.map((f) => f.name).join(", ") !== m[3]) return null;
  return { words: (m[1] ?? "").trim(), files };
}

/** The words of a message with its note removed, for a preview or a copy. */
export function wordsOfMessage(content: string): string {
  const note = parseUploadNote(content);
  if (!note) return content;
  if (note.words) return note.words;
  return note.files.length === 1
    ? `📎 ${note.files[0].name}`
    : `📎 ${note.files.length} files`;
}

/** A file size for a chip: B, KB or MB. */
export function formatFileSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const IMAGE_KINDS = [".png", ".jpg", ".jpeg", ".gif", ".webp"];

/** Whether a name is an image that a browser draws as a thumbnail. */
export function isImageName(name: string): boolean {
  return IMAGE_KINDS.includes(kindOf(name));
}
