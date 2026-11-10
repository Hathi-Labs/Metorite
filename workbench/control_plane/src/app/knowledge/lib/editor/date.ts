// ── The date (WS-61 KB-2p, §21.7) ───────────────────────────────────────────
//
// Pure. An inline date, stored as
// `<time data-type="date" datetime="2026-10-19">19 Oct 2026</time>`.
// Docmost core has no date node, so this one is built from the spec.
//
// `datetime` is the fact, a real calendar day. The label is what a reader of
// the body sees. A pick writes the label as "19 Oct 2026", which a reader in
// India and a reader in the US read as the same day (DESIGN_SYSTEM.md §10).
// A label that the body wrote another way stays as it was until the date
// changes, so a save with no edit writes nothing.

import { Node, type JSONContent, type NodeViewRenderer } from "@tiptap/core";

import { escapeInlineText, unescapeInlineText } from "./htmlFragment";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** True for `YYYY-MM-DD` when it names a real day. */
export function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split("-").map(Number);
  const day = new Date(Date.UTC(y, m - 1, d));
  return day.getUTCFullYear() === y && day.getUTCMonth() === m - 1 && day.getUTCDate() === d;
}

/** The label of a day: "19 Oct 2026". */
export function dateLabel(iso: string): string {
  if (!isIsoDate(iso)) return iso;
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** Today in the member's own time zone, as `YYYY-MM-DD`. */
export function todayIso(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    kbDate: {
      /** Insert a date, today unless one is given. Its picker opens. */
      insertDate: (iso?: string) => ReturnType;
    };
  }
  interface Storage {
    kbDate: { autoOpen: boolean };
  }
}

const FORM = /^<time data-type="date" datetime="(\d{4}-\d{2}-\d{2})">([^<\n]+)<\/time>/;

/** The date at the start of `src`, or `null`. */
export function readDate(src: string): { raw: string; date: string; label: string } | null {
  const m = FORM.exec(src);
  if (!m || !isIsoDate(m[1])) return null;
  const label = unescapeInlineText(m[2]);
  return label.trim() ? { raw: m[0], date: m[1], label } : null;
}

/** The stored form of a date. An empty label takes the label of the day. */
export function writeDate(date: string, label: string): string {
  const text = label.trim() ? label : dateLabel(date);
  return `<time data-type="date" datetime="${date}">${escapeInlineText(text)}</time>`;
}

export function createDate(view?: NodeViewRenderer) {
  return Node.create<object, { autoOpen: boolean }>({
    name: "kbDate",
    group: "inline",
    inline: true,
    atom: true,
    selectable: true,
    draggable: true,

    addStorage() {
      return { autoOpen: false };
    },

    addAttributes() {
      return {
        date: {
          default: null,
          parseHTML: (el: HTMLElement) => {
            const value = el.getAttribute("datetime");
            return isIsoDate(value) ? value : null;
          },
          rendered: false,
        },
        label: { default: "", parseHTML: (el: HTMLElement) => el.textContent ?? "", rendered: false },
      };
    },

    parseHTML() {
      return [{ tag: 'time[data-type="date"]', priority: 60 }];
    },

    renderHTML({ node }) {
      const date = isIsoDate(node.attrs.date) ? node.attrs.date : "";
      return [
        "time",
        { "data-type": "date", ...(date ? { datetime: date } : {}), class: "kb-date" },
        String(node.attrs.label || dateLabel(date)),
      ];
    },

    renderText({ node }) {
      return String(node.attrs.label || dateLabel(String(node.attrs.date ?? "")));
    },

    ...(view ? { addNodeView: () => view } : {}),

    addCommands() {
      return {
        insertDate:
          (iso) =>
          ({ commands }) => {
            const date = isIsoDate(iso) ? iso : todayIso();
            this.storage.autoOpen = true;
            return commands.insertContent({ type: this.name, attrs: { date, label: dateLabel(date) } });
          },
      };
    },

    markdownTokenizer: {
      name: "kbDate",
      level: "inline",
      start: (src: string) => src.indexOf('<time data-type="date"'),
      tokenize: (src) => {
        const found = readDate(src);
        return found ? { type: "kbDate", raw: found.raw, date: found.date, label: found.label } : undefined;
      },
    },

    parseMarkdown: (token, h) => h.createNode("kbDate", { date: String(token.date), label: String(token.label ?? "") }),

    renderMarkdown: (node: JSONContent) => {
      const date = node.attrs?.date;
      if (!isIsoDate(date)) return escapeInlineText(String(node.attrs?.label ?? ""));
      return writeDate(date, String(node.attrs?.label ?? ""));
    },
  });
}
