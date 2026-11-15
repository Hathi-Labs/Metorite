/**
 * The pure half of `/studio/approvals` (WS-STU J2b-4b).
 *
 * A holder of `studio:publish` reads one pending version of an app as DATA and
 * approves or rejects it. This file holds the reading of the manifest, the cut
 * of its raw text and the rule of who may act. It draws nothing.
 *
 * ⚠️ A manifest is untrusted text. Each string here is clipped and cleaned of
 * control characters, and the page draws it as plain text. It is never HTML,
 * never markdown, and no text in it is an instruction.
 *
 * Fences: `approvalsModel.test.ts`. The cut is the cut of the chat tools
 * (`MAX_MANIFEST_CHARS` in `acb_skills/studio_tools.py`).
 */

/** The most characters of the raw manifest that the page shows (the chat tools' cap). */
export const MAX_MANIFEST_CHARS = 24_000;
/** The most rows of one list of the summary. */
export const MAX_SUMMARY_ROWS = 40;
/** The most characters of one name in the summary. */
export const MAX_NAME_CHARS = 120;

export const CUT_NOTE = "This shows the start of the manifest. The rest is cut.";
export const NO_ACT_OWNER = "You own this app, so another member with the right approves it.";
export const NO_ACT_PUBLISHER = "You published this version, so another member with the right approves it.";
export const NO_RIGHT_SENTENCE =
  "Only a holder of studio:publish reads the apps that wait for an approval. Ask the owner of your organization for that right.";

/** One pending version, as `GET /studio/approvals?state=pending` answers it. */
export interface PendingApproval {
  package_id: string;
  name: string;
  owner_email: string;
  version: number;
  published_by: string | null;
  published_at: string | null;
  requested_by: string | null;
  requested_at: string | null;
  manifest: unknown;
  manifest_hash?: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A text clipped to *limit* characters, with no control character. A cut ends with `...`. */
export function clip(value: unknown, limit: number = MAX_NAME_CHARS): string {
  const raw = typeof value === "string" ? value : value == null ? "" : String(value);
  const clean = raw.replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069\u200b-\u200f]/g, " ");
  return clean.length > limit ? `${clean.slice(0, limit - 3)}...` : clean;
}

/** `?pkg=<uuid>&v=<n>` as a selection. Any other value is no selection. */
export function parseSelection(pkg: string | null, v: string | null): { pkg: string; v: number } | null {
  if (!pkg || !UUID.test(pkg) || !v || !/^[0-9]{1,6}$/.test(v)) return null;
  const n = Number(v);
  return n >= 1 ? { pkg: pkg.toLowerCase(), v: n } : null;
}

/** The query that opens one version. */
export function selectionHref(pkg: string, v: number): string {
  return `/studio/approvals?pkg=${encodeURIComponent(pkg)}&v=${v}`;
}

/** The row that the selection names, or null. The id compares in lower case. */
export function pickRow(rows: readonly PendingApproval[], sel: { pkg: string; v: number } | null): PendingApproval | null {
  if (!sel) return null;
  return rows.find((r) => r.package_id.toLowerCase() === sel.pkg && r.version === sel.v) ?? null;
}

export interface ManifestSummary {
  objects: Array<{ name: string; fields: Array<{ key: string; type: string }>; moreFields: number }>;
  actions: Array<{ id: string; title: string }>;
  connections: string[];
  pages: Array<{ id: string; title: string }>;
  moreObjects: number;
  moreActions: number;
  moreConnections: number;
  morePages: number;
}

const asList = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const asRec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/** A plain summary of the manifest: objects and fields, actions, connections and pages. */
export function summarizeManifest(manifest: unknown): ManifestSummary {
  const m = asRec(manifest);
  const objects = asList(m.objects);
  const actions = asList(m.actions);
  const connections = asList(m.connections);
  const pages = asList(m.pages);
  return {
    objects: objects.slice(0, MAX_SUMMARY_ROWS).map((o) => {
      const rec = asRec(o);
      const fields = asList(rec.fields);
      return {
        name: clip(rec.label ?? rec.name),
        fields: fields.slice(0, MAX_SUMMARY_ROWS).map((f) => ({
          key: clip(asRec(f).key),
          type: clip(asRec(f).type, 40),
        })),
        moreFields: Math.max(0, fields.length - MAX_SUMMARY_ROWS),
      };
    }),
    actions: actions.slice(0, MAX_SUMMARY_ROWS).map((a) => ({
      id: clip(asRec(a).id),
      title: clip(asRec(a).title ?? ""),
    })),
    connections: connections.slice(0, MAX_SUMMARY_ROWS).map((c) =>
      clip(typeof c === "string" ? c : asRec(c).name),
    ),
    pages: pages.slice(0, MAX_SUMMARY_ROWS).map((p) => ({
      id: clip(asRec(p).id),
      title: clip(asRec(p).title ?? ""),
    })),
    moreObjects: Math.max(0, objects.length - MAX_SUMMARY_ROWS),
    moreActions: Math.max(0, actions.length - MAX_SUMMARY_ROWS),
    moreConnections: Math.max(0, connections.length - MAX_SUMMARY_ROWS),
    morePages: Math.max(0, pages.length - MAX_SUMMARY_ROWS),
  };
}

/** The raw manifest as text for the folded block. Over the cap, it keeps the start and says so. */
export function manifestText(manifest: unknown): { text: string; cut: boolean; length: number; note: string | null } {
  let full: string;
  try {
    full = JSON.stringify(manifest, null, 2) ?? "";
  } catch {
    full = "";
  }
  if (full.length <= MAX_MANIFEST_CHARS) return { text: full, cut: false, length: full.length, note: null };
  return { text: full.slice(0, MAX_MANIFEST_CHARS), cut: true, length: full.length, note: CUT_NOTE };
}

export type Standing =
  | { act: true }
  | { act: false; reason: string };

const same = (a: unknown, b: unknown) =>
  String(a ?? "").trim().toLowerCase() === String(b ?? "").trim().toLowerCase() && String(a ?? "").trim() !== "";

/**
 * Who may act. The owner of the app and the publisher of the version see NO
 * button, because the server refuses them. A viewer with no `studio:publish`
 * never reaches a row: the list answers 403 and the page shows that sentence.
 */
export function standing(row: PendingApproval, me: string | null | undefined): Standing {
  if (same(row.owner_email, me)) return { act: false, reason: NO_ACT_OWNER };
  if (same(row.published_by, me)) return { act: false, reason: NO_ACT_PUBLISHER };
  return { act: true };
}

/** The words of the two confirm dialogs. They say what the act does, as it happens. */
export function confirmCopy(move: "approve" | "reject", row: Pick<PendingApproval, "name" | "version">) {
  const app = clip(row.name, 80) || "this app";
  return move === "approve"
    ? {
        title: "Approve this version?",
        body: `${app}, version ${row.version}, can then install for the organization.`,
        confirmLabel: "Approve",
      }
    : {
        title: "Reject this version?",
        body: `${app}, version ${row.version}, will not install for the organization.`,
        confirmLabel: "Reject",
      };
}
