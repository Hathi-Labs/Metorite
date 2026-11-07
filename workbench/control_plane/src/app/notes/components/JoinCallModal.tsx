"use client";

/**
 * JoinCallModal — send the notetaker bot to join a live call by link (spec
 * §3.13, Phase 1). Paste one meeting URL per line to fan several notetakers out
 * to concurrent meetings. Honest about the consent posture and about the "not
 * set up yet" state when no provider key is configured.
 */

import { useCloseOnBarTap } from "@/lib/phoneBarTap";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useState } from "react";
import { botJoin, getBotConfig } from "../lib/api";

interface JoinCallModalProps {
  onClose: () => void;
  /** Called after at least one bot was dispatched, so the page can refresh. */
  onJoined: () => void;
  /** Send the bot to a meeting that already exists (one you prepared), rather
   *  than creating a fresh one — otherwise its agenda and briefing are
   *  stranded on a different meeting. Only one link may be sent this way: a
   *  prepared meeting is one meeting. */
  meetingId?: string;
  defaultTitle?: string;
}

type LineResult = { url: string; ok: boolean; error?: string };

export default function JoinCallModal({
  onClose,
  onJoined,
  meetingId,
  defaultTitle,
}: JoinCallModalProps) {
  // A bar tap closes the window (MOB-9).
  useCloseOnBarTap(onClose);
  const [urls, setUrls] = useState("");
  const [busy, setBusy] = useState(false);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [results, setResults] = useState<LineResult[] | null>(null);

  useEffect(() => {
    void getBotConfig()
      .then((c) => setConfigured(c.configured))
      .catch(() => setConfigured(false));
  }, []);

  async function dispatch() {
    const all = urls
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    // Attaching to a prepared meeting is inherently single-link — fanning out
    // would point several bots at the same meeting row.
    const links = meetingId ? all.slice(0, 1) : all;
    if (links.length === 0) return;
    setBusy(true);
    setResults(null);
    const settled = await Promise.all(
      links.map(async (url): Promise<LineResult> => {
        try {
          await botJoin(url, defaultTitle, meetingId);
          return { url, ok: true };
        } catch (e) {
          return { url, ok: false, error: String(e instanceof Error ? e.message : e) };
        }
      })
    );
    setResults(settled);
    setBusy(false);
    if (settled.some((r) => r.ok)) onJoined();
  }

  const anySuccess = results?.some((r) => r.ok) ?? false;

  return (
    <div
      className="fixed inset-0 z-[70] grid place-items-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-card p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary/15 text-primary">
            <Icon name="Video" className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-bold text-foreground">
              Send the notetaker to a call
            </h2>
            <p className="text-[11px] text-muted-foreground">
              Google Meet — it joins, records, and writes notes. (Zoom/Teams
              are on the roadmap.)
            </p>
          </div>
          <Button variant="ghost" size="icon-xs" layout="" onClick={onClose} aria-label="Close">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        {configured === false ? (
          <div className="rounded-lg bg-warning/10 px-3 py-2.5 text-xs text-warning">
            <span className="flex items-start gap-1.5">
              <Icon name="AlertCircle" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                The meeting notetaker isn&apos;t set up yet. An admin needs to
                connect the self-hosted meeting-bot worker
                (<code>MEETING_BOT_URL</code>) on the server to enable it.
              </span>
            </span>
          </div>
        ) : (
          <>
            <textarea
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
              rows={3}
              autoFocus
              placeholder={
                "Paste a meeting link…\nhttps://meet.google.com/abc-defg-hij\n(one per line to join several at once)"
              }
              className="w-full resize-y rounded-xl border border-border bg-background p-3 font-mono text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-ring"
            />

            {results && (
              <div className="mt-2 space-y-1.5">
                {results.map((r, i) => (
                  <div key={i} className="text-[11px]">
                    <p
                      className={`truncate ${
                        r.ok ? "text-success" : "text-destructive"
                      }`}
                      title={r.url}
                    >
                      {r.ok ? "✓ Joining" : "✗ Failed"} — {r.url}
                    </p>
                    {!r.ok && r.error && (
                      <p className="mt-0.5 rounded-md bg-destructive/10 px-2 py-1 leading-relaxed text-destructive">
                        {r.error}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* The single most common failure isn't a bug: Google refuses
                guests outright when nobody is in the call yet. Saying so here
                prevents the failure instead of explaining it afterwards. */}
            <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-warning/10 px-2.5 py-1.5 text-[10px] leading-relaxed text-warning">
              <Icon name="AlertCircle" className="mt-0.5 h-3 w-3 shrink-0" />
              <span>
                Join the call yourself first, then admit the notetaker when it
                knocks — Google turns guests away when no one is in the meeting
                yet. To skip that, sign the notetaker into a Google account and
                invite it like a person.
              </span>
            </p>

            <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
              The bot joins named so everyone can see it. Recording others may
              require their consent depending on where you are — get it first.
            </p>

            <div className="mt-3 flex justify-end gap-2">
              <Button variant="text" size="none" layout="" onClick={onClose} className="px-3 py-1.5 text-xs">
                {anySuccess ? "Done" : "Cancel"}
              </Button>
              <Button layout="inline-flex items-center" onClick={() => void dispatch()} disabled={busy || configured === null || !urls.trim()}>
                {busy ? (
                  <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Icon name="Video" className="h-3.5 w-3.5" />
                )}
                {busy ? "Sending…" : "Send notetaker"}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
