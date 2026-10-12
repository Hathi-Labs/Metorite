"use client";

/**
 * One custom field value, drawn by the rules of `../lib/fields.ts`
 * (WS-53 CRM-Q4). The record sheet and the list cells share it, so a date or
 * a link reads the same in both. Since CRM-X4 it also draws the fixed email
 * and phone fields and the facts of the record header.
 */

import { fieldText, type FieldValue as Value } from "../lib/fields";

/** The most chips a record page shows, and the most a list cell shows. */
export const CHIP_CAP_RECORD = 20;
export const CHIP_CAP_CELL = 3;

export default function FieldValue({
  value,
  compact = false,
}: {
  value: Value;
  /** A list cell. It shows fewer chips. */
  compact?: boolean;
}) {
  switch (value.kind) {
    case "chips":
      return (
        <span className="flex flex-wrap gap-1">
          {value.items.slice(0, compact ? CHIP_CAP_CELL : CHIP_CAP_RECORD).map((item, i) => (
            <span
              key={`${item}-${i}`}
              className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-foreground"
            >
              {item}
            </span>
          ))}
          {value.items.length > (compact ? CHIP_CAP_CELL : CHIP_CAP_RECORD) && (
            <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-muted-foreground">
              +{value.items.length - (compact ? CHIP_CAP_CELL : CHIP_CAP_RECORD)}
            </span>
          )}
        </span>
      );
    case "link":
      return (
        <a
          href={value.href}
          target="_blank"
          rel="noopener noreferrer"
          className="break-all text-primary underline underline-offset-2"
          onClick={(e) => e.stopPropagation()}
        >
          {value.text}
        </a>
      );
    case "mailto":
    case "tel":
      // A mail or a call opens the member's own app, so the link stays in
      // this tab (CRM-X4 item 6).
      return (
        <a
          href={value.href}
          className="break-all text-primary underline underline-offset-2"
          onClick={(e) => e.stopPropagation()}
        >
          {value.text}
        </a>
      );
    case "text":
      return value.wrap ? (
        <span className="whitespace-pre-wrap break-words">{value.text}</span>
      ) : (
        <>{value.text}</>
      );
    default:
      return <>{fieldText(value)}</>;
  }
}
