"use client";

/**
 * One custom field value, drawn by the rules of `../lib/fields.ts`
 * (WS-53 CRM-Q4). The record sheet and the list cells share it, so a date or
 * a link reads the same in both.
 */

import { fieldText, type FieldValue as Value } from "../lib/fields";

export default function FieldValue({ value }: { value: Value }) {
  switch (value.kind) {
    case "chips":
      return (
        <span className="flex flex-wrap gap-1">
          {value.items.map((item, i) => (
            <span
              key={`${item}-${i}`}
              className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-foreground"
            >
              {item}
            </span>
          ))}
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
