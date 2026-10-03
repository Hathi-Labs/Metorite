"use client";

import Icon from "@/components/Icon";
import { useState } from "react";
import { Email } from "../lib/types";
import { useEmailStore } from "../lib/emailStore";
import { presetForLabel, presetHex, deterministicPreset } from "../lib/labelColors";
import { ColorSwatch, LabelColorGrid } from "./LabelChip";

/**
 * Label picker — toggle the account's labels on a message (multi-select, stays
 * open) and create new ones. Applied labels show a check. Each label has a
 * colour swatch that opens a palette; the choice syncs to the provider (Gmail
 * label / Outlook category colour). Writes sync via the store.
 */
export function LabelMenu({
  email,
  embedded = false,
}: {
  email: Email;
  /** When rendered inside another popover/flyout (e.g. the right-click submenu),
   *  drop this component's own panel chrome + fixed width so it doesn't create a
   *  box-in-a-box with a nested scrollbar. */
  embedded?: boolean;
}) {
  const { availableLabels, labelColors, applyLabel, setLabelColor, viewAll, selectedAccountId } =
    useEmailStore();
  // The colours are those of the selected mailbox. A mail of another mailbox
  // (All inboxes) gets labels but no colour edit here (EM-T8d review).
  const canColor = !viewAll || email.accountId === selectedAccountId;
  const [newLabel, setNewLabel] = useState("");
  const [newColor, setNewColor] = useState<string | null>(null);
  const [pickNew, setPickNew] = useState(false);
  // Which label's colour palette is currently expanded (null = none).
  const [openColorFor, setOpenColorFor] = useState<string | null>(null);
  const applied = new Set(email.categories || []);

  const create = () => {
    const name = newLabel.trim();
    if (!name) return;
    applyLabel(email.id, name, true);
    if (newColor && canColor) setLabelColor(name, newColor, email.accountId);
    setNewLabel("");
    setNewColor(null);
    setPickNew(false);
  };

  // Preview colour for the create-row swatch: the explicit pick, else a
  // deterministic preview of the typed name (so it's never an empty swatch).
  const newPreviewHex = presetHex(
    newColor || deterministicPreset(newLabel.trim() || "label")
  );

  return (
    <div
      className={
        embedded
          ? "text-xs"
          : "w-60 bg-popover border border-border rounded-lg shadow-xl py-1 text-xs"
      }
    >
      <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        Labels
      </div>
      {/* Only the label list scrolls (and only when it overflows) — the create
          row below stays pinned. */}
      <div className="max-h-56 overflow-y-auto">
        {availableLabels.length === 0 ? (
          <div className="px-3 py-1.5 text-muted-foreground">No labels yet</div>
        ) : (
          availableLabels.map((name) => {
            const on = applied.has(name);
            const open = openColorFor === name;
            return (
              <div key={name}>
                <div className="w-full flex items-center gap-2 px-3 py-1.5 text-foreground/80 hover:bg-secondary transition-colors">
                  <button
                    onClick={() => applyLabel(email.id, name, !on)}
                    className="flex items-center gap-2 flex-1 min-w-0 text-left hover:text-foreground"
                  >
                    <span
                      className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${
                        on
                          ? "bg-primary border-primary text-primary-foreground"
                          : "border-muted-foreground/40"
                      }`}
                    >
                      {on && <Icon name="Check" size={11} />}
                    </span>
                    <span className="truncate">{name}</span>
                  </button>
                  {canColor && (
                    <ColorSwatch
                      name={name}
                      title={open ? "Close colours" : "Set colour"}
                      onClick={() => setOpenColorFor(open ? null : name)}
                    />
                  )}
                </div>
                {open && canColor && (
                  <div className="px-3 pb-1.5">
                    <LabelColorGrid
                      value={presetForLabel(name, labelColors)}
                      onPick={(c) => {
                        setLabelColor(name, c, email.accountId);
                        setOpenColorFor(null);
                      }}
                    />
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
      <div className="border-t border-border mt-1 pt-1 px-2 pb-1">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            title="Pick a colour for the new label"
            onClick={() => setPickNew((v) => !v)}
            style={{ backgroundColor: newPreviewHex }}
            className="w-3.5 h-3.5 rounded-full border border-black/20 ring-1 ring-inset ring-white/20 flex-shrink-0 hover:scale-110 transition-transform"
          />
          <input
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                create();
              }
            }}
            placeholder="Create label…"
            className="flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground outline-none px-1 py-1"
          />
          <button
            onClick={create}
            disabled={!newLabel.trim()}
            title="Create label"
            className="text-primary hover:opacity-80 disabled:opacity-40"
          >
            <Icon name="Plus" size={13} />
          </button>
        </div>
        {pickNew && canColor && (
          <div className="pt-1">
            <LabelColorGrid
              value={newColor}
              onPick={(c) => {
                setNewColor(c);
                setPickNew(false);
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
