"use client";

import Icon from "@/components/Icon";
import {
  LABEL_PALETTE,
  chipColors,
  presetForLabel,
  presetHex,
  textOn,
} from "../lib/labelColors";
import { useEmailStore } from "../lib/emailStore";

/**
 * A label/category chip rendered in its assigned colour. Colours come from the
 * store's `labelColors` map (provider-synced), falling back to a deterministic
 * colour for labels with none set. Optionally clickable (e.g. list filter).
 */
export function LabelChip({
  name,
  onClick,
  active = false,
  icon = false,
  title,
  className = "text-[11px] px-2 py-0.5",
}: {
  name: string;
  onClick?: (e: React.MouseEvent) => void;
  /** Emphasise as the active filter (adds a ring). */
  active?: boolean;
  /** Show a leading tag glyph. */
  icon?: boolean;
  title?: string;
  className?: string;
}) {
  const labelColors = useEmailStore((s) => s.labelColors);
  const { bg, text } = chipColors(name, labelColors);
  const interactive = !!onClick;
  return (
    <span
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      title={title}
      onClick={onClick}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.(e as unknown as React.MouseEvent);
              }
            }
          : undefined
      }
      style={{
        backgroundColor: bg,
        color: text,
        // Active filter: a ring in the chip's own colour, offset by the bg.
        boxShadow: active
          ? `0 0 0 1.5px var(--background), 0 0 0 3px ${bg}`
          : undefined,
      }}
      className={`inline-flex items-center gap-1 rounded-full font-medium transition-opacity ${
        interactive ? "cursor-pointer hover:opacity-90" : ""
      } ${className}`}
    >
      {icon && <Icon name="Tag" size={11} />}
      {name}
    </span>
  );
}

/** A small round swatch showing a label's effective colour; opens the picker. */
export function ColorSwatch({
  name,
  onClick,
  title = "Set colour",
  menuStop = false,
}: {
  name: string;
  onClick?: (e: React.MouseEvent) => void;
  title?: string;
  /** A stop of the keys of a `role="menu"` panel (`MessageActions`). */
  menuStop?: boolean;
}) {
  const labelColors = useEmailStore((s) => s.labelColors);
  const hex = presetHex(presetForLabel(name, labelColors));
  return (
    <button
      type="button"
      title={title}
      aria-label={`${title}: ${name}`}
      onClick={onClick}
      data-menu-stop={menuStop ? "" : undefined}
      tabIndex={menuStop ? -1 : undefined}
      style={{ backgroundColor: hex }}
      className="w-3.5 h-3.5 rounded-full border border-black/20 ring-1 ring-inset ring-white/20 flex-shrink-0 hover:scale-110 transition-transform outline-none focus-visible:ring-2 focus-visible:ring-ring"
    />
  );
}

/** Palette grid for picking a label colour (rendered inline under a row). */
export function LabelColorGrid({
  value,
  onPick,
}: {
  value?: string | null;
  onPick: (preset: string) => void;
}) {
  return (
    <div className="grid grid-cols-5 gap-1.5 p-2 bg-secondary/50 rounded-md">
      {LABEL_PALETTE.map((c) => {
        const selected = value === c.id;
        return (
          <button
            key={c.id}
            type="button"
            title={c.name}
            onClick={(e) => {
              e.stopPropagation();
              onPick(c.id);
            }}
            style={{
              backgroundColor: c.hex,
              color: textOn(c.hex),
              boxShadow: selected
                ? "0 0 0 2px var(--background), 0 0 0 4px var(--foreground)"
                : undefined,
            }}
            className="w-5 h-5 rounded-full flex items-center justify-center hover:scale-110 transition-transform"
          >
            {selected && <Icon name="Check" size={11} />}
          </button>
        );
      })}
    </div>
  );
}
