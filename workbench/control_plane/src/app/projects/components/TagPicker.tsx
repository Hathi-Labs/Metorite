"use client";

/**
 * Projects · the tag picker in the task panel (WS-27m).
 *
 * Typing filters the project's registry; Enter takes the highlighted
 * suggestion, or creates a tag when nothing matches. **Creating is shown, not
 * silent**: auto-registration is deliberate — a two-step errand is how tagging
 * gets abandoned — but a text box that quietly mints a tag per typo is how a
 * tag set rots, so the moment says "create".
 *
 * A comma is a separator, never part of a tag. The filter parameter is CSV, so
 * a tag containing one could never be filtered by; treating it as a separator
 * here means one can never be stored in the first place.
 */

import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useState } from "react";

import {
  TAG_COLORS,
  type TagColor,
  type TagRow,
  addTag,
  autoTagHue,
  chipClass,
  swatchClass,
  registryOf,
  removeTag,
  suggest,
  wouldCreate,
} from "../lib/tags";

interface Props {
  value: string[];
  registry: TagRow[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
  /**
   * Drop the "Tags" heading and the "None yet." line (WS-27n's bulk bar,
   * 2026-09-20).
   *
   * The panel is a labelled block in a column. The bulk bar is a row of
   * controls where every field is already named by its placeholder, and a
   * heading over each one would double the bar's height for nothing. The
   * BEHAVIOUR is identical either way — this only removes two lines of
   * chrome, which is why it is a flag here and not a second component.
   */
  compact?: boolean;
  placeholder?: string;
  ariaLabel?: string;
  /**
   * Register a brand-new tag in the project's registry, with its colour.
   *
   * Optional, and absent is meaningful: the bulk bar can edit a selection
   * spanning several projects, so there is no ONE registry to create in.
   * Without it the picker behaves exactly as it did — the name goes on the
   * task and the server registers it at the column default.
   */
  onCreate?: (name: string, color: TagColor) => void | Promise<unknown>;
}

export function TagPicker({
  value,
  registry,
  disabled = false,
  onChange,
  compact = false,
  placeholder = "Add a tag…",
  ariaLabel = "Add a tag",
  onCreate,
}: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  /** The field the portalled list measures from. See `AnchoredPanel`. */
  const [field, setField] = useState<HTMLInputElement | null>(null);

  const lookup = registryOf(registry);
  const options = suggest(query, registry, value);
  const creating = wouldCreate(query, lookup);
  const colorOf = (name: string) =>
    registry.find((t) => t.name.toLowerCase() === name.toLowerCase())?.color;

  const commit = (raw: string, hue?: TagColor) => {
    const next = addTag(value, raw, lookup);
    if (next !== value) onChange(next);
    // ⚠️ Registering is BEST EFFORT and never blocks the add. The server
    // auto-registers an unknown name anyway (at the column default), so a
    // failed create costs the colour and nothing else — whereas awaiting it
    // would make adding a tag wait on a second request, which is the errand
    // this picker exists to avoid.
    if (hue && onCreate && wouldCreate(raw, lookup)) {
      void Promise.resolve(onCreate(raw.trim(), hue)).catch(() => {});
    }
    setQuery("");
  };

  return (
    <div>
      {compact ? null : (
        <span className="text-xs text-muted-foreground">Tags</span>
      )}
      <div className={compact ? "flex flex-wrap gap-1 empty:hidden" : "mt-1 flex flex-wrap gap-1"}>
        {value.map((name) => (
          <span
            key={name}
            className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] ${chipClass(colorOf(name))}`}
          >
            {name}
            <button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${name}`}
              onClick={() => onChange(removeTag(value, name))}
              className="opacity-70 hover:opacity-100"
            >
              <Icon name="X" size={10} />
            </button>
          </span>
        ))}
        {value.length === 0 && !compact ? (
          <span className="text-xs text-muted-foreground">None yet.</span>
        ) : null}
      </div>

      <div className={compact && value.length === 0 ? "relative" : "relative mt-1"}>
        <Input
          ref={setField}
          inputSize="sm"
          disabled={disabled}
          value={query}
          aria-label={ariaLabel}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          // A blur has to outlive the mousedown on a suggestion, or clicking
          // one closes the list before the click lands.
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw.includes(",")) {
              // Pasting "bug, ops, urgent" adds three tags rather than making
              // one impossible-to-filter tag out of the lot.
              for (const part of raw.split(",")) commit(part);
              return;
            }
            setQuery(raw);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit(
                options.length && !creating ? options[0].name : query,
                creating ? autoTagHue(query) : undefined,
              );
            } else if (e.key === "Escape") {
              setOpen(false);
            } else if (e.key === "Backspace" && query === "" && value.length) {
              // The convention every chip input has: backspace on an empty box
              // takes the last chip off.
              onChange(value.slice(0, -1));
            }
          }}
        />

        {/* ⚠️ PORTALLED. Measured in the task panel on 2026-09-20: this
            list spanned y486–615 inside a scrolling box that ended at y552,
            so its last row and the "Create" action were off screen. The
            owner reported the picker as not showing up properly. See
            `AnchoredPanel`. */}
        <AnchoredPanel
          anchor={field}
          open={open && (options.length > 0 || creating)}
          maxHeight={256}
        >
          <ul className="overflow-hidden rounded-lg">
            {options.map((option) => (
              <li key={option.id}>
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => commit(option.name)}
                  className="flex w-full items-center justify-between px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
                >
                  <span
                    className={`rounded-md px-1.5 py-0.5 ${chipClass(option.color)}`}
                  >
                    {option.name}
                  </span>
                  <span className="text-muted-foreground">
                    {option.task_count ?? 0}
                  </span>
                </button>
              </li>
            ))}
            {creating ? (
              /* ⚠️ The colour is OFFERED here, never demanded.
                 
                 This file's header says auto-registration is deliberate,
                 because "a two-step errand is how tagging gets abandoned".
                 A modal asking for a hue on every new tag is that errand.
                 So Enter still creates in one keystroke — it just no longer
                 creates a grey tag, because `autoTagHue` picks a stable
                 colour from the name. The six swatches are there for the
                 member who cares, one click, without leaving the list.
                 Curating afterwards is `TagManager`'s job. */
              <li className="border-t border-border p-1">
                <Button
                  variant="ghost"
                  size="sm"
                  icon="Plus"
                  className="w-full justify-start"
                  onMouseDown={(e: React.MouseEvent) => e.preventDefault()}
                  onClick={() => commit(query, autoTagHue(query))}
                >
                  Create “{query.trim()}”
                </Button>
                <div className="mt-1 flex items-center gap-1 px-2 pb-0.5">
                  <span className="text-[11px] text-muted-foreground">
                    Colour
                  </span>
                  {TAG_COLORS.map((hue) => {
                    const auto = hue === autoTagHue(query);
                    return (
                      <button
                        key={hue}
                        type="button"
                        // The name says which hue AND whether it is the one
                        // Enter would take — a swatch row is unreadable to a
                        // screen reader otherwise.
                        aria-label={
                          auto ? `${hue} (chosen by default)` : `Create it ${hue}`
                        }
                        title={hue}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => commit(query, hue)}
                        className={`h-4 w-4 rounded-full ${swatchClass(hue)} ${
                          auto
                            ? "ring-2 ring-ring ring-offset-1 ring-offset-card"
                            : "opacity-70 hover:opacity-100"
                        }`}
                      />
                    );
                  })}
                </div>
              </li>
            ) : null}
          </ul>
        </AnchoredPanel>
      </div>
    </div>
  );
}
