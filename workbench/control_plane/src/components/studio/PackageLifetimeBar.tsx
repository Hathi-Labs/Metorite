"use client";

/**
 * The end-date bar of an app page (`package_host.md` §6.2, PKG-10e-2, task 2971).
 *
 * The owner of a private install sees "Ends in N days" with Keep, or "Archived" with Restore. Keep
 * removes the end date and the bar goes away. Restore starts the 30 days again, and the bar reads
 * the lifetime once more. An error shows the sentence of the gateway under the bar. A read that
 * fails, an install that is not private and a member who is not the owner draw nothing.
 * Fence: `PackageLifetimeBar.test.tsx`.
 */
import { useState } from "react";

import Button from "@/components/ui/Button";
import {
  type FlowState, barState, barText, createLifetimeFlow, getLifetime, lifetimeKey,
} from "@/lib/studio/packageLifetime";
import { useCachedResource } from "@/lib/useCachedResource";

export default function PackageLifetimeBar({ pkg }: { pkg: string }) {
  const lifetime = useCachedResource(lifetimeKey(pkg), () => getLifetime(pkg));
  const [view, setView] = useState<FlowState>({ busy: false, kept: false, problem: null });
  const [flow] = useState(() => createLifetimeFlow(pkg, { onChange: setView }));

  const read = lifetime.data;
  const install = read?.kind === "bar" ? read.install : null;
  const state = barState(install);
  const words = install ? barText(install) : null;
  if (view.kept || !install || state === "none" || !words) return null;
  const { busy, problem } = view;

  return (
    <div>
      <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-card p-3 sm:p-4">
        <span className="text-sm text-foreground">{words}</span>
        {state === "ends" ? (
          <Button type="button" variant="secondary" size="sm" loading={busy} onClick={() => flow.press("keep")}>Keep</Button>
        ) : (
          <Button type="button" variant="primary" size="sm" loading={busy} onClick={async () => { if (await flow.press("restore")) lifetime.refresh(); }}>Restore</Button>
        )}
      </div>
      {problem ? <p role="alert" className="mt-1 text-xs text-destructive">{problem}</p> : null}
    </div>
  );
}
