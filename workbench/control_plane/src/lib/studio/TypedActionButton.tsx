"use client";

/**
 * A typed-action button on a Studio page (STU-D1, DESIGN_SYSTEM.md §12).
 *
 * It runs `door.run(id, args)` and shows the four beats: the pending spinner
 * on the button, a guard on the second press, the result, and a plain line
 * when the run fails. After `ok` it drops the cached reads of the door, so
 * the bound blocks read again.
 *
 * ⚠️ The shared `useAction` seam (§12) is not built yet. This component holds
 * its own state on purpose. When `useAction` exists, replace the state here.
 */
import { useRef, useState } from "react";

import Button, { type ButtonProps } from "@/components/ui/Button";
import { invalidate } from "@/lib/dataCache";
import {
  type ParsedAction, type StudioDataDoor, doorPrefix,
} from "./dataDoor";

export const ACTION_FAILED = "That did not work. Try again.";

/** Run once and report. Pure of React, so the tests can drive it. */
export async function runTyped(
  door: StudioDataDoor,
  action: Extract<ParsedAction, { kind: "typed" }>,
): Promise<{ ok: boolean; message: string | null }> {
  try {
    const res = await door.run(action.id, action.args);
    if (res && res.ok === true) {
      invalidate(doorPrefix(door));
      return { ok: true, message: null };
    }
    const msg = res && typeof res.message === "string" && res.message.trim() ? res.message : ACTION_FAILED;
    return { ok: false, message: msg };
  } catch {
    // Never `String(err)`: the member reads plain words.
    return { ok: false, message: ACTION_FAILED };
  }
}

/** Run `fn` unless one run is pending. A second press does nothing (M3). */
export async function pressOnce(
  flag: { current: boolean },
  fn: () => Promise<void>,
): Promise<boolean> {
  if (flag.current) return false;
  flag.current = true;
  try {
    await fn();
  } finally {
    flag.current = false;
  }
  return true;
}

export function TypedActionButton({
  door, action, label, variant, size = "sm",
}: {
  door: StudioDataDoor;
  action: Extract<ParsedAction, { kind: "typed" }>;
  label: string;
  variant: ButtonProps["variant"];
  size?: ButtonProps["size"];
}): React.ReactElement {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // A ref guards the second press before the state commits.
  const busy = useRef(false);

  const press = async () => {
    await pressOnce(busy, async () => {
      setPending(true);
      setFailure(null);
      const out = await runTyped(door, action);
      setPending(false);
      setFailure(out.ok ? null : out.message);
    });
  };

  return (
    <span className="inline-flex items-center gap-2">
      <Button
        type="button"
        variant={variant}
        size={size}
        loading={pending}
        aria-busy={pending || undefined}
        onClick={() => { void press(); }}
      >
        {label}
      </Button>
      {failure && <span className="text-xs text-destructive" role="alert">{failure}</span>}
    </span>
  );
}
