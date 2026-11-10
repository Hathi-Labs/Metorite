"use client";

/**
 * Messages Metorite starts, on My Profile (WS-59 RE-1a).
 *
 * Spec: `project-docs/specs/proactive_reach.md` §16.1.
 *
 * Shows what Reach would send now on WhatsApp: the daily brief and the
 * items that would interrupt. "Send my brief now" sends the member's own
 * brief to their own WhatsApp. Every decision is in `../lib/reach.ts`.
 * ⚠️ It draws NOTHING unless the preview answers 200, so a member for whom
 * Reach is dark never sees it.
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import { type ReachView, fetchReachPreview, reachView, sendBriefNow, sendResult } from "../lib/reach";

export function ReachSection() {
  const [view, setView] = useState<ReachView>({ kind: "hidden" });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const reload = useCallback(async () => {
    setView(reachView(await fetchReachPreview()));
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (view.kind === "hidden") return null;

  async function onSend() {
    setBusy(true);
    setResult(null);
    try {
      setResult(sendResult(await sendBriefNow()));
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-border p-3">
      <div className="flex items-center gap-2 text-xs font-medium text-foreground">
        <Icon name="Bell" className="h-4 w-4" />
        Messages Metorite starts
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Metorite sends you a brief at the start of your working day, and a
        few short messages a day when something needs you now. Nothing is
        sent outside your working hours.
      </p>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <Preview title="Your brief now" text={view.brief} empty="Nothing needs you right now." />
        <Preview
          title="Would interrupt you now"
          text={view.interrupts}
          empty="Nothing new is urgent."
        />
      </div>
      {view.held && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          These wait until your next message to Metorite on WhatsApp, then go
          out together.
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          icon="Send"
          loading={busy}
          disabled={view.blocked !== null}
          onClick={() => void onSend()}
        >
          Send my brief now
        </Button>
        {view.blocked && (
          <span className="text-[11px] text-muted-foreground">{view.blocked}</span>
        )}
        {result && (
          <span
            className={`text-[11px] ${result.ok ? "text-muted-foreground" : "text-destructive"}`}
            role={result.ok ? "status" : "alert"}
          >
            {result.message}
          </span>
        )}
      </div>
    </section>
  );
}

function Preview({ title, text, empty }: { title: string; text: string | null; empty: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium text-muted-foreground">{title}</div>
      <pre className="mt-1 whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-2 font-sans text-xs text-foreground">
        {text ?? empty}
      </pre>
    </div>
  );
}
