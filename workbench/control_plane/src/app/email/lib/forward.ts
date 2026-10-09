/**
 * The Forward of the reading pane, with the files of the email.
 *
 * Follow-up 4 of #766 (`email_app_master_plan.md` §15). The pane built the
 * forward in the browser, cleared every file, and sent a new mail, so the
 * PDF of a quote never reached the next person. The pane now sends
 * `POST /email/forward` (`gateway/routes/email/transport/forward.py`), which
 * carries the original files and builds the forwarded header itself.
 *
 * Pure and framework-free, so the request and the words of each refusal
 * have a test of their own (`forward.test.ts`).
 */

import type { Attachment, EmailAccount } from "./types";

/** One file of the email, as the forward compose shows it: a chip. */
export interface ForwardFile {
  id: string;
  filename: string;
  sizeBytes: number;
  /** The member keeps it (the default) or takes it out of the forward. */
  checked: boolean;
}

/** The files of the email, each one kept. */
export function forwardFilesOf(attachments: readonly Attachment[] | undefined): ForwardFile[] {
  return (attachments ?? [])
    .filter((a) => a && a.id)
    .map((a) => ({ id: a.id, filename: a.filename || "file", sizeBytes: a.sizeBytes || 0, checked: true }));
}

/** "29 KB", "2.0 MB": the size on a chip. "" when the size is not known. */
export function fileSizeText(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface ForwardInput {
  /** The local id of the email to forward. */
  messageId: string;
  /** The mailbox that holds the email. The route refuses any other one. */
  accountId: string;
  to: string[];
  cc: string[];
  bcc: string[];
  /** The member's words above the forwarded email, signature included. */
  note: string;
  files: readonly ForwardFile[];
}

/** The body of `POST /email/forward`, in the gateway's own field names. */
export interface ForwardBody {
  message_id: string;
  account_id: string;
  to: string[];
  cc?: string[];
  bcc?: string[];
  note?: string;
  include_attachments: boolean;
  attachment_ids?: string[];
}

/**
 * The request that the pane sends. Every file kept is `include_attachments`
 * alone, so Outlook takes its native forward (Graph copies the files at the
 * server). No file kept is `include_attachments: false`. Some files kept
 * name each one in `attachment_ids`.
 *
 * A pane that holds no file list (the detail of the email did not load)
 * asks for every file: the member took nothing out, and the route sends
 * what the email holds.
 */
export function forwardRequest(input: ForwardInput): ForwardBody {
  const kept = input.files.filter((f) => f.checked);
  const body: ForwardBody = {
    message_id: input.messageId,
    account_id: input.accountId,
    to: input.to,
    include_attachments: input.files.length === 0 || kept.length > 0,
  };
  if (input.cc.length) body.cc = input.cc;
  if (input.bcc.length) body.bcc = input.bcc;
  if (input.note.trim()) body.note = input.note;
  if (kept.length > 0 && kept.length < input.files.length) {
    body.attachment_ids = kept.map((f) => f.id);
  }
  return body;
}

/** Outlook forwards every file of an email or none (`OUTLOOK_SUBSET`). */
export const OUTLOOK_ALL_OR_NONE =
  "Outlook forwards all the files of an email, or none of them.";

/** The send that the pane stopped, because some files of an Outlook mail
 *  were taken out. The notice of the chips holds the two choices. */
export const OUTLOOK_SUBSET_UNSENT =
  "Nothing was sent. Choose Keep every file or Forward without files above.";

/**
 * True when the forward goes out of an Outlook mailbox with some files kept
 * and some taken out. The route answers that with a 422, so the pane asks
 * the member first.
 */
export function outlookSubset(
  provider: EmailAccount["provider"] | undefined,
  files: readonly ForwardFile[],
): boolean {
  if (provider !== "microsoft") return false;
  const kept = files.filter((f) => f.checked).length;
  return kept > 0 && kept < files.length;
}

/** The words of a refused forward, and whether a forward without files can help. */
export interface ForwardFailure {
  text: string;
  /** Show "Forward without files": the files are the cause. */
  offerNoFiles: boolean;
}

/**
 * Markers of the route's own 422 details (`forward.py`). The fence in
 * `forward.test.ts` reads the Python source and fails when a marker leaves
 * its detail.
 */
export const FORWARD_DETAIL_MARKERS = {
  outlookSubset: "On an Outlook mailbox",
  imapFiles: "files of an IMAP mailbox",
  noBytes: "gave no bytes for the file",
} as const;

function statusOf(err: unknown): number | undefined {
  if (typeof err !== "object" || err === null) return undefined;
  const status = (err as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

function detailOf(err: unknown): string {
  if (typeof err !== "object" || err === null) return "";
  const message = (err as { message?: unknown }).message;
  // A validation error carries a list, which reads "[object Object]".
  if (typeof message !== "string" || message.startsWith("[object") || /^Gateway error \d+$/.test(message)) {
    return "";
  }
  return message.trim();
}

/** The words that the pane shows for each answer of the route that is not 200. */
export function forwardFailure(err: unknown): ForwardFailure {
  const status = statusOf(err);
  const detail = detailOf(err);
  if (status === 413) {
    return {
      text: detail || "This email and its files are too large to forward. Nothing was sent.",
      offerNoFiles: true,
    };
  }
  if (status === 422) {
    if (detail.includes(FORWARD_DETAIL_MARKERS.outlookSubset)) {
      return { text: `${OUTLOOK_ALL_OR_NONE} Nothing was sent.`, offerNoFiles: true };
    }
    if (detail.includes(FORWARD_DETAIL_MARKERS.imapFiles) || detail.includes(FORWARD_DETAIL_MARKERS.noBytes)) {
      return { text: detail, offerNoFiles: true };
    }
    return { text: detail || "The mail provider refused the forward. Nothing was sent.", offerNoFiles: false };
  }
  if (status === 401) {
    return { text: "Reconnect this mailbox to forward from it. Nothing was sent.", offerNoFiles: false };
  }
  if (status === 404) {
    return {
      text: detail && detail !== "Email not found"
        ? detail
        : "This email is no longer in the mailbox, so nothing was sent.",
      offerNoFiles: false,
    };
  }
  if (status === 429) {
    return { text: detail || "The mail provider asked for a pause. Nothing was sent. Try again later.", offerNoFiles: false };
  }
  if (status === 502) {
    const base = detail || "The mail provider did not answer, so nothing was sent.";
    return { text: `${base} Try again in a moment.`, offerNoFiles: false };
  }
  return { text: detail || "The forward failed. Nothing was sent.", offerNoFiles: false };
}
