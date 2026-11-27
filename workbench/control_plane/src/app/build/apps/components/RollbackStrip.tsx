"use client";

/**
 * The confirm strip under a version row: "Make vN live?". When the request
 * fails, the strip stays open and says why (task 3976).
 */

import Button from "@/components/ui/Button";
import AppIcon from "@/components/Icon";

export default function RollbackStrip({
  version,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  version: number;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] text-muted-foreground flex-1">
          Make v{version} live?
        </span>
        <Button size="none" radius="keep" layout="flex items-center" onClick={onConfirm} disabled={busy} className="text-[10px] rounded-md px-2 py-1 gap-1">
          {busy && <AppIcon name="Loader2" className="w-3 h-3 animate-spin" />}
          Confirm
        </Button>
        <button
          onClick={onCancel}
          className="text-[10px] rounded-md border border-border px-2 py-1 text-muted-foreground hover:text-foreground tech-transition"
        >
          Cancel
        </button>
      </div>
      {error && (
        <div role="alert" className="flex items-start gap-1.5 text-[11px] text-destructive">
          <AppIcon name="AlertTriangle" className="w-3 h-3 mt-0.5 shrink-0" />
          <span className="min-w-0 break-words">{error}</span>
        </div>
      )}
    </div>
  );
}
