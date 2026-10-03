"use client";

/**
 * Pick a slot of the categorical ramp — the one colour picker of the product.
 *
 * ⚠️ **A SLOT, never a colour** (DESIGN_SYSTEM rule 7). Each swatch draws the
 * hue that the active theme gives `--cat-<n>`, so a pick stays right in light
 * and in dark, and under any accent. A hex field would be the one thing rule
 * 1 refuses. Space settings (migration 194) and the mailbox colour (EM-T8b,
 * migration 227) both draw this, so the two cannot drift.
 *
 * `value` and `onChange` are 0-based ramp indexes, as `accentForSlot` takes
 * them. A stored column is 1-based, and the caller converts in one place.
 */

import { CATEGORICAL_SLOTS, accentForSlot } from "@/lib/categorical";

export interface SlotPickerProps {
  /** The 0-based index that is selected. */
  value: number;
  onChange: (index: number) => void;
  /** The accessible name of the group, for example "Mailbox colour". */
  label: string;
}

export default function SlotPicker({ value, onChange, label }: SlotPickerProps) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1.5">
      {Array.from({ length: CATEGORICAL_SLOTS }, (_, index) => (
        <button
          key={index}
          type="button"
          role="radio"
          aria-checked={index === value}
          aria-label={`Colour ${index + 1}`}
          onClick={() => onChange(index)}
          className={`flex h-8 w-8 items-center justify-center rounded-md tech-transition ${
            index === value ? "ring-2 ring-primary" : "hover:bg-muted"
          }`}
        >
          <span className={`h-4 w-4 rounded-full ${accentForSlot(index).dot}`} />
        </button>
      ))}
    </div>
  );
}
