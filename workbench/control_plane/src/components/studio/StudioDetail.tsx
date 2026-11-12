"use client";

/**
 * The Detail block (`ai_studio.md` §5.1, slice STU-D2a): a read-only sheet
 * of one record. It shows each field by its type, in the order of `fields`.
 * A select draws as a chip (`Badge`), an address as a link only when it
 * passes the same check as the server (`safeHref`), and a missing value as a
 * dash. A value reads as the Table reads it: the words come from
 * `displayValue`, and a select or person draws through `ValueElement`, as a
 * Table cell does. The sheet is generic. A caller adds its own actions beside it.
 */
import { ValueElement } from "@/components/CardFieldValue";
import { MarkdownBody } from "@/components/MarkdownMessage";
import { displayValue, labelOf, safeHref } from "@/lib/studio/blocks";
import type { FieldSpec } from "@/lib/studio/fieldCheck";

function Value({ field, value }: { field: FieldSpec; value: unknown }) {
  const text = displayValue(field, value);
  if (text === "") return <span className="text-muted-foreground">—</span>;
  const items = Array.isArray(value) ? value : [value];
  switch (field.type) {
    case "select":
    case "multiSelect":
      return (
        <span className="inline-flex flex-wrap gap-1">
          {items.map((v, i) => <ValueElement key={i} item={{ text: String(v), named: true }} kind="tag" />)}
        </span>
      );
    case "url":
      return (
        <span className="flex flex-col gap-0.5 break-all">
          {items.map((v, i) => {
            const href = safeHref(v);
            return href
              ? <a key={i} href={href} target="_blank" rel="noopener noreferrer"
                className="text-primary hover:underline">{href}</a>
              : <span key={i}>{String(v)}</span>;
          })}
        </span>
      );
    case "person":
      return (
        <span className="inline-flex flex-wrap gap-1">
          {items.map((v, i) => <ValueElement key={i} item={{ text: String(v), named: true }} kind="person" />)}
        </span>
      );
    case "email":
      return <span className="break-all">{text}</span>;
    case "longText":
      return <span className="whitespace-pre-wrap break-words">{text}</span>;
    case "richText":
      // The one markdown seam. It makes no element of raw HTML, and the check refuses a tag.
      // A heading in the text stays at the size of the sheet, so it never outranks its label.
      return (
        <div className="flex min-w-0 flex-col gap-1 break-words [&_h1:not(:first-child)]:!mt-2 [&_h1:first-child]:!mt-0 [&_h1]:!text-xs [&_h2:not(:first-child)]:!mt-2 [&_h2:first-child]:!mt-0 [&_h2]:!text-xs [&_h3:not(:first-child)]:!mt-2 [&_h3:first-child]:!mt-0 [&_h3]:!text-xs [&_h4:not(:first-child)]:!mt-2 [&_h4:first-child]:!mt-0 [&_h4]:!text-xs [&_h5:not(:first-child)]:!mt-2 [&_h5:first-child]:!mt-0 [&_h5]:!text-xs [&_h6:not(:first-child)]:!mt-2 [&_h6:first-child]:!mt-0 [&_h6]:!text-xs">
          {items.map((v, i) => <MarkdownBody key={i} content={String(v)} />)}
        </div>
      );
    case "barcode":
      return <span className="break-all font-mono">{text}</span>;
    default:
      return <span className="break-words">{text}</span>;
  }
}

export interface StudioDetailProps {
  fields: readonly FieldSpec[];
  record: Record<string, unknown>;
  title?: string;
}

export function StudioDetail({ fields, record, title }: StudioDetailProps) {
  return (
    <section aria-label={title ?? "Record"} className="space-y-2">
      {title && <h2 className="text-sm font-semibold text-foreground">{title}</h2>}
      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-[max-content_1fr]">
        {fields.map((f) => (
          <div key={f.key} data-field={f.key} data-type={f.type}
            className="grid grid-cols-subgrid gap-y-0.5 sm:col-span-2">
            <dt className="font-medium text-muted-foreground">{labelOf(f)}</dt>
            <dd className="min-w-0 text-foreground"><Value field={f} value={record[f.key]} /></dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default StudioDetail;
