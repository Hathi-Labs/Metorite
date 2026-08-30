"use client";

/**
 * Space Settings — name, icon and icon colour (owner directive 2026-08-31,
 * migration 194).
 *
 * Reached by right-clicking a space in the tree. A SPACE only: a project
 * and a subproject spend their marker slot on run state, and a folder draws
 * a folder, so an icon set anywhere else would be stored and never shown —
 * which is why the server refuses it rather than accepting it quietly.
 *
 * ⚠️ **A NAME and a SLOT, never a colour** (DESIGN_SYSTEM rule 7). The
 * picker below writes an icon name into the themed registry and an index
 * into the `--cat-1..12` ramp, so the active theme decides both which pack
 * draws the glyph and what the hue actually is, in light and in dark. A
 * colour picker offering hex values would be the one thing rule 1 refuses,
 * and it would be unreachable by any later re-theme.
 *
 * 🔭 FUTURE (recorded in `project_management_app.md` §5.1): a space will
 * also carry an owning team, and then appear in that team's Center. That is
 * a third field in this dialog when the teams exist — not a second dialog.
 */
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { CATEGORICAL_SLOTS, accentForSlot } from "@/lib/categorical";
import { useEffect, useState } from "react";

import type { ProjectRow } from "../lib/api";
import { LEVEL_ICONS, SPACE_ICON_CHOICES, spaceMarker } from "../lib/tree";

export interface SpaceSettingsProps {
  /** The space being edited, or null when the dialog is closed. */
  space: ProjectRow | null;
  onClose: () => void;
  /** Commit. The page owns the PATCH, the toast and the refetch. */
  onSave: (
    space: ProjectRow,
    values: { name: string; icon: string; icon_slot: number }
  ) => void;
}

export default function SpaceSettings({
  space,
  onClose,
  onSave,
}: SpaceSettingsProps) {
  const [name, setName] = useState("");
  const [icon, setIcon] = useState<string>(LEVEL_ICONS.space);
  const [slot, setSlot] = useState(0);

  // Re-seed every time a DIFFERENT space opens the dialog. Keyed on the id
  // rather than on `space` itself: the page refetches the tree after a save,
  // which hands us a new object for the same row, and reseeding on that
  // would discard an edit in progress.
  useEffect(() => {
    if (!space) return;
    const marker = spaceMarker(space);
    setName(space.name);
    setIcon(marker.icon);
    setSlot(marker.slot);
  }, [space?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!space) return null;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    // The column is 1-based and `accentForSlot` is 0-based; this is the one
    // place the two conventions meet, so the conversion lives here and
    // `spaceMarker` performs the inverse.
    onSave(space, { name: trimmed, icon, icon_slot: slot + 1 });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Space settings"
      description="The name, the icon and the colour this space wears in the sidebar."
      icon="Settings"
      size="md"
    >
      <form onSubmit={submit} className="space-y-4 p-4">
        <div className="space-y-1.5">
          <label
            htmlFor="space-name"
            className="text-xs font-medium text-muted-foreground"
          >
            Name
          </label>
          <Input
            id="space-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Space name"
          />
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Icon</p>
          <div
            role="radiogroup"
            aria-label="Space icon"
            className="grid max-h-44 grid-cols-8 gap-1 overflow-y-auto pr-1"
          >
            {SPACE_ICON_CHOICES.map((choice) => (
              <button
                key={choice}
                type="button"
                role="radio"
                aria-checked={choice === icon}
                aria-label={choice}
                onClick={() => setIcon(choice)}
                className={`flex items-center justify-center rounded-md p-2 tech-transition ${
                  choice === icon
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted"
                }`}
              >
                <Icon name={choice} className="h-4 w-4" />
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Colour</p>
          {/* Twelve themed slots, never a hex field. Each swatch draws in
              the hue the ACTIVE theme gives that slot, so what you pick here
              is what every theme will honour. */}
          <div
            role="radiogroup"
            aria-label="Icon colour"
            className="flex flex-wrap gap-1.5"
          >
            {Array.from({ length: CATEGORICAL_SLOTS }, (_, index) => (
              <button
                key={index}
                type="button"
                role="radio"
                aria-checked={index === slot}
                aria-label={`Colour ${index + 1}`}
                onClick={() => setSlot(index)}
                className={`flex h-8 w-8 items-center justify-center rounded-md tech-transition ${
                  index === slot ? "ring-2 ring-primary" : "hover:bg-muted"
                }`}
              >
                <span
                  className={`h-4 w-4 rounded-full ${accentForSlot(index).dot}`}
                />
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Preview</p>
          {/* The sidebar row, drawn exactly as the tree draws it. A picker
              that previews nothing makes people save to find out. */}
          <div className="flex items-center gap-2 rounded-md bg-muted/50 px-2 py-1.5 text-sm">
            <Icon
              name={icon}
              className={`h-4 w-4 shrink-0 ${accentForSlot(slot).text}`}
            />
            <span className="truncate font-medium">{name || space.name}</span>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit">Save</Button>
        </div>
      </form>
    </Modal>
  );
}
