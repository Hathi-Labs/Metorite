// ── The space forms of Knowledge (WS-61 KB-2q) ──────────────────────────────
//
// Pure. The rules of the New space form and the Space settings form: who may
// open them, which icons they offer, and the body each one sends. The limits
// mirror `SpaceCreate` and `SpacePatch` in the gateway (`spaces.py`).
//
// ⚠️ The stand-in "My notes" space has no row (`labels.isPendingMe`) and a
// `me` space cannot be renamed. Neither gets a settings form, and nothing
// sends the stand-in id to the gateway.

import { isPendingMe } from "./labels";
import type { Space } from "./types";

/** The gateway limits (`SpaceCreate`, `SpacePatch`). */
export const SPACE_NAME_MAX = 120;
export const SPACE_PURPOSE_MAX = 300;

/** The advice that sits above the New space form. */
export const SPACE_ADVICE = "Make a space when a different group of people needs to see it. For a new topic, make a page.";

/** The icon names the forms offer. Each is a real icon of the registry (`spaceForm.test.ts`). */
export const SPACE_ICON_CHOICES: readonly string[] = [
  "Users", "Building2", "Briefcase", "BookOpen",
  "Rocket", "Target", "Flag", "Star",
  "Megaphone", "ShoppingCart", "Headphones", "Wallet",
  "Code", "Cpu", "Wrench", "Shield",
  "Palette", "Camera", "Lightbulb", "Truck",
  "Factory", "FlaskConical", "Landmark", "Globe",
  "Layers", "Package", "Mail", "Phone",
  "Newspaper", "Calendar", "Brain", "Sparkles",
];

/** The icon that a new space starts with: the one of a team. */
export const DEFAULT_SPACE_ICON = "Users";

/**
 * True when the member may change this space: a manager of it, and a space
 * that has a row. The `me` space and the stand-in never qualify.
 */
export function canEditSpace(space: Pick<Space, "id" | "kind" | "level">): boolean {
  return space.level === "manage" && space.kind !== "me" && !isPendingMe(space.id);
}

/** What the form holds. */
export interface SpaceDraft {
  name: string;
  icon: string;
  purpose: string;
}

/** The body of `POST /knowledge/spaces`. Names, icon and purpose only: no grants (KB-2r). */
export function createSpaceBody(draft: SpaceDraft): { name: string; icon: string; purpose: string } {
  return {
    name: draft.name.split(/\s+/).filter(Boolean).join(" "),
    icon: draft.icon.trim(),
    purpose: draft.purpose.trim(),
  };
}

/** Only the fields that changed, or `null` when nothing did. */
export function patchSpaceBody(saved: SpaceDraft, draft: SpaceDraft): Partial<SpaceDraft> | null {
  const out: Partial<SpaceDraft> = {};
  const name = draft.name.split(/\s+/).filter(Boolean).join(" ");
  if (name && name !== saved.name) out.name = name;
  if (draft.icon.trim() !== saved.icon.trim()) out.icon = draft.icon.trim();
  if (draft.purpose.trim() !== saved.purpose.trim()) out.purpose = draft.purpose.trim();
  return Object.keys(out).length ? out : null;
}
