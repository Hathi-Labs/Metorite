/**
 * The pure half of the Gallery block (`ai_studio.md` §14 D19, slice STU-D19a).
 *
 * A gallery draws the records of a source as cards in a grid. A card has a cover, a title and
 * at most three fields. This file reads the block's props, picks the cover of a record from its
 * `file` fields, and gives the image address of a card. It has no React import. The words of a
 * card come from `displayValue` (`blocks.ts`), so no second formatter exists.
 * Fence: `galleryModel.test.ts`.
 *
 * ⚠️ THE LAZY LOAD. `coverSrc` gives an address only for a card that the observer reported as
 * seen. So the first markup holds no image address, and a card loads its image when it comes
 * into view. The address comes from the door (`fileAddress`), and only the sample door has one
 * (D19a). The real door has none until D24 serves the bytes of a file, so a live card draws its
 * placeholder.
 */
import { labelOf, parseFields } from "./blocks";
import { rowId } from "./boardModel";
import type { FileAddress } from "./dataDoor";
import type { FieldSpec } from "./fieldCheck";

/** A card shows at most this many fields under its title. */
export const MAX_CARD_FIELDS = 3;

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** A field whose name says that it holds the state of a record. */
const STATUS_NAME = /^(?:status|stage|state)$/i;

export interface GalleryConfig {
  fields: FieldSpec[];
  titleField: FieldSpec | null;
  /** The file fields that may give the cover, in order: the named cover field, else each file field. */
  coverFields: FieldSpec[];
  cardFields: FieldSpec[];
  /** The card field that draws as a status chip, or null. A single select named status, stage or state. */
  statusField: FieldSpec | null;
  /** The start of the record link of a card, or null for a card with no link (as a Board card, STU-AF5). */
  openBase: string | null;
}

export type ParsedGallery = { ok: true; config: GalleryConfig } | { ok: false; reason: string };

/** Read the props of a gallery node. A model can write them later (D19c), so each part is checked. */
export function parseGallery(data: unknown): ParsedGallery {
  const d = isObject(data) ? data : {};
  const fields = parseFields(d.fields);
  if (fields.length === 0) return { ok: false, reason: "This gallery needs the fields of its object." };
  const byKey = (k: unknown) => (typeof k === "string" ? fields.find((f) => f.key === k) : undefined);

  let coverFields = fields.filter((f) => f.type === "file");
  if (d.coverField !== undefined && d.coverField !== null && d.coverField !== "") {
    const named = byKey(d.coverField);
    if (!named || named.type !== "file") {
      const name = named ? labelOf(named) : String(d.coverField);
      return { ok: false, reason: `This gallery takes its cover from a file field. "${name}" is not one.` };
    }
    coverFields = [named];
  }

  const titleField = byKey(d.titleField)
    ?? fields.find((f) => f.key === "title")
    ?? fields.find((f) => f.type === "text")
    ?? null;
  const cardFields: FieldSpec[] = [];
  for (const k of Array.isArray(d.cardFields) ? d.cardFields : []) {
    const f = byKey(k);
    // A file is the cover, and its words would be an id. So a card field is never a file.
    if (f && f.type !== "file" && f.key !== titleField?.key && !cardFields.includes(f)) cardFields.push(f);
  }
  const shown = cardFields.slice(0, MAX_CARD_FIELDS);
  const statusField = shown.find((f) => f.type === "select" && f.many !== true
    && (STATUS_NAME.test(f.key) || STATUS_NAME.test(labelOf(f)))) ?? null;
  return {
    ok: true,
    config: {
      fields, titleField, coverFields, cardFields: shown, statusField,
      openBase: typeof d.openBase === "string" && d.openBase ? d.openBase : null,
    },
  };
}

/** The node that draws a Gallery: the `gallery` template on the D1 bind path. A page builds it. */
export function galleryBlock(
  data: {
    fields: readonly FieldSpec[];
    titleField?: string;
    coverField?: string;
    cardFields?: readonly string[];
    openBase?: string;
    title?: string;
  },
  source: { action: string; args?: Record<string, unknown> },
): { type: "template"; props: Record<string, unknown> } {
  return { type: "template", props: { name: "gallery", source, data } };
}

/** The key of a card for the observer: the record id, or the place of a row with no id. */
export function cardKey(row: Record<string, unknown>, index: number): string {
  const id = rowId(row);
  return id !== null ? `id:${id}` : `at:${index}`;
}

/** What the observer has reported. A `ReadonlySet<string>` of card keys is one. */
export interface Seen {
  has(key: string): boolean;
}

/** Every card counts as seen. For a browser with no IntersectionObserver: `loading="lazy"` alone holds the load. */
export const EVERY_CARD: Seen = { has: () => true };

/** The set after a report of the observer. The same set when no key is new, so React draws nothing again. */
export function markSeen(seen: ReadonlySet<string>, keys: readonly string[]): ReadonlySet<string> {
  const fresh = keys.filter((k) => !seen.has(k));
  if (fresh.length === 0) return seen;
  return new Set([...seen, ...fresh]);
}

/** The file id of one value: `{id}`, or the first `{id}` of a list. */
function fileIdOf(value: unknown): string | null {
  for (const v of Array.isArray(value) ? value : [value]) {
    if (isObject(v) && typeof v.id === "string" && v.id) return v.id;
  }
  return null;
}

/**
 * THE one pick of a cover. The first file in the cover fields, in their order. Null for a record
 * with no file there, and for a record whose door left the field out because C8 hides it.
 */
export function coverFile(config: GalleryConfig, row: Record<string, unknown>): { field: FieldSpec; id: string } | null {
  for (const field of config.coverFields) {
    const id = fileIdOf(row[field.key]);
    if (id) return { field, id };
  }
  return null;
}

/** A path of this site, or the data of a raster image. Never another site and never a script. */
const SAFE_ADDRESS = /^(?:\/(?![/\\])[^\s\\]*|data:image\/(?:png|jpeg|gif|webp|avif);base64,[A-Za-z0-9+/]+=*)$/;

/** The address when a card may load it, else null. */
export function safeAddress(src: unknown): string | null {
  return typeof src === "string" && SAFE_ADDRESS.test(src) ? src : null;
}

/**
 * The image address of a card, or null for its placeholder. Null for a card that the observer
 * has not reported, for a record with no cover, for a door with no `fileAddress`, and for an
 * address of another site.
 */
export function coverSrc(
  config: GalleryConfig,
  row: Record<string, unknown>,
  key: string,
  seen: Seen,
  address: FileAddress | null | undefined,
): string | null {
  if (!address || !seen.has(key)) return null;
  const file = coverFile(config, row);
  if (!file) return null;
  try {
    return safeAddress(address(file.id));
  } catch {
    return null;
  }
}

/**
 * The `statusAccent` input of a status value: its name and its place among the options, as a
 * Board lane gets it. So one option has one hue on the Board and in the Gallery. Null for a value
 * that is no option.
 */
export function statusInput(field: FieldSpec, value: unknown): { name: string; index: number; total: number } | null {
  if (typeof value !== "string" || value === "") return null;
  const options = [...new Set(field.options ?? [])];
  const index = options.indexOf(value);
  return index < 0 ? null : { name: value, index, total: options.length };
}
