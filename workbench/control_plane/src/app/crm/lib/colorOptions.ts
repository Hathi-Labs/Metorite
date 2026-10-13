// ── The colour picker of a stage row (WS-53 CRM-C2 review) ─────────────────
//
// The picker offers `ACCENT_HUES`, the six hues the board can draw. A row can
// hold a colour name that the old CRM map accepted and the picker does not
// offer, such as `orange`, `teal` or `sky`. Without a matching option the
// picker opened with nothing selected, so the admin could not see what is
// stored. So the stored name comes first, marked "custom", with the hue that
// the board draws for it.

import type { SelectOption } from "@/components/ui/SelectButton";
import { ACCENT_HUES, resolveHue } from "@/lib/statusAccent";

const BASE: SelectOption[] = ACCENT_HUES.map((color) => ({ value: color, label: color }));

export function colorOptions(stored: string | null | undefined): SelectOption[] {
  const name = stored?.trim();
  if (!name || (ACCENT_HUES as readonly string[]).includes(name)) return BASE;
  return [
    { value: name, label: name, hint: `custom, draws ${resolveHue({ color: name })}` },
    ...BASE,
  ];
}
