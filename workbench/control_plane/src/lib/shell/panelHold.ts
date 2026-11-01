/**
 * Which shell surface shows a panel's list in place of its Modal (owner,
 * 2026-11-01, prototype). The Ask bar shows the runs out of itself ("the chat
 * box can expand in height"), and the sidebar shows Needs you beside its row
 * ("have it on the sidebar only"). While a surface holds a list, that Modal
 * panel (`ActivityHost`, `ShellBellHost`) stays shut. When no surface holds
 * it (the assistant hides the bar, or a phone has no sidebar), the Modal opens
 * as before. The open state stays in the two panel stores (`openActivity`,
 * `openBell`), so every trigger is unchanged.
 *
 * Its own module, so the panels read it without importing the surfaces.
 */

import { useSyncExternalStore } from "react";

export type HeldPanel = "runs" | "needs";

const _holds: Record<HeldPanel, number> = { runs: 0, needs: 0 };
const _listeners = new Set<() => void>();

function _emit(): void {
  _listeners.forEach((l) => l());
}

/** A surface holds this list from now. Returns the release. */
export function holdPanel(kind: HeldPanel): () => void {
  _holds[kind] += 1;
  _emit();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    _holds[kind] -= 1;
    _emit();
  };
}

function subscribe(listener: () => void): () => void {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

/** True while a surface holds this list, so its Modal must stay shut. */
export function usePanelHeld(kind: HeldPanel): boolean {
  return useSyncExternalStore(subscribe, () => _holds[kind] > 0, () => false);
}
