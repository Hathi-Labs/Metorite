"use client";

/**
 * The Publish dialog of the Studio IDE (`ai_studio.md` §14, slice G5). It shows what changes in the
 * LIVE app before anything goes live: the diff of the live version against the stored draft, in plain
 * words. Publish sends the stored draft with its hash (`based_on`), so a draft that changed after the
 * dialog opened gets 409 and nothing is published. The honest line says what a publish makes.
 */
import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import type { Problem } from "@/lib/studio/draftProblems";
import { cleanName, diffLines, diffManifests } from "@/lib/studio/manifestDiff";
import { previewHref } from "@/lib/studio/packagePreview";
import {
  HONEST_LINE, MAX_NOTES, guardedClose, getVersionManifest, publishDraft, publishedWords, type PublishResult,
} from "@/lib/studio/publish";
import { StudioOff } from "@/lib/studio/realDoor";

export interface PublishDialogProps {
  open: boolean;
  pkg: string;
  name: string;
  manifest: unknown;
  hash: string | null;
  liveVersion: number | null;
  onClose: () => void;
  /** A publish landed: the page reads the draft again, so the badge follows. */
  onPublished: () => void;
  /** The server refused: the Problems panel shows each error at its path. */
  onProblems: (problems: Problem[]) => void;
  onReload: () => void;
}

type Live = { state: "loading" } | { state: "ready"; manifest: unknown } | { state: "error"; text: string };

export default function PublishDialog(p: PublishDialogProps) {
  const [live, setLive] = useState<Live>({ state: "loading" });
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PublishResult | null>(null);
  const [all, setAll] = useState(false);
  // The draft the member saw when the box opened. A later poll never swaps it, so Publish sends THIS
  // hash and a draft that changed meanwhile gets 409.
  const [snap, setSnap] = useState<{ hash: string | null; manifest: unknown; liveVersion: number | null } | null>(null);
  const { open, pkg, liveVersion } = p;

  // Reading the live version again each time the box opens keeps the diff true.
  useEffect(() => {
    if (!open) return;
    // `busy` is not reset here: the box cannot close while a publish runs, so a reopen never meets a stale one.
    setResult(null); setNotes(""); setAll(false);
    setSnap({ hash: p.hash, manifest: p.manifest, liveVersion });
    if (liveVersion === null) { setLive({ state: "ready", manifest: null }); return; }
    setLive({ state: "loading" });
    let stop = false;
    getVersionManifest(pkg, liveVersion).then(
      (manifest) => { if (!stop) setLive({ state: "ready", manifest }); },
      (e: unknown) => {
        if (stop) return;
        setLive({ state: "error", text: e instanceof StudioOff ? "This app is not available to you any more. Go back to Studio." : e instanceof Error ? e.message : "The live version did not load." });
      },
    );
    return () => { stop = true; };
    // A publish moves `liveVersion`, and the box must keep its result, so only an open or a new app reads again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pkg]);

  const seen = snap ?? { hash: p.hash, manifest: p.manifest, liveVersion };
  const seenLive = seen.liveVersion;
  const done = result?.kind === "published" ? result : null;
  const next = done ? done.version : (seenLive ?? 0) + 1;
  const first = seenLive === null;
  const changes = live.state === "ready" ? diffManifests(live.manifest, seen.manifest) : [];
  const lines = diffLines(changes, all ? 100 : 12, first);
  const nothing = live.state === "ready" && !first && changes.length === 0;
  const canPublish = live.state === "ready" && !nothing && !!seen.hash && !busy && !done;

  const send = () => {
    if (!seen.hash || busy) return;
    setBusy(true); setResult(null);
    publishDraft(pkg, notes, seen.hash).then(
      (r) => {
        setBusy(false); setResult(r);
        if (r.kind === "published") p.onPublished();
        if (r.kind === "problems") p.onProblems(r.problems);
      },
      (e: unknown) => {
        setBusy(false);
        setResult({ kind: "failed", message: e instanceof StudioOff ? "This app is not available to you any more. Nothing was published. Go back to Studio." : "Studio could not publish. Nothing was published. Press Publish again." });
      },
    );
  };

  return (
    <Modal open={open} onClose={() => guardedClose(busy, p.onClose)} title={`Publish «${cleanName(p.name) || "this app"}» as version ${next}`} icon="Rocket" size="md">
      <div className="space-y-3 px-3 py-3 text-xs" data-publish-dialog>
        {!done ? (
          <section aria-label="What changes in the live app">
            <h3 className="mb-1 font-medium text-foreground">What changes in the live app</h3>
            {live.state === "loading" ? <p role="status" className="text-muted-foreground">Reading the live version…</p> : null}
            {live.state === "error" ? <p role="alert" className="text-destructive">{live.text}</p> : null}
            {nothing ? <p className="text-muted-foreground">Nothing would change. The draft equals live version {seenLive}.</p> : null}
            {live.state === "ready" && !nothing && lines.length === 0 ? (
              <p className="text-muted-foreground">The app has no record types, views or actions yet. Publish still makes version {next}.</p>
            ) : null}
            {lines.length > 0 ? (
              <>
                <p className="mb-1 text-muted-foreground">{first ? "This first version makes:" : `This version, against live version ${seenLive}:`}</p>
                <ul className="list-disc space-y-0.5 pl-4 text-foreground">
                  {lines.map((l, i) => <li key={i} className="break-words">{l}</li>)}
                </ul>
                {!all && changes.length > 12 ? (
                  <button type="button" className="mt-1 text-primary underline" onClick={() => setAll(true)}>Show all ({changes.length - 12} more)</button>
                ) : null}
              </>
            ) : null}
          </section>
        ) : null}
        <p className="rounded-md border border-border bg-secondary/50 px-2.5 py-2 text-muted-foreground">{HONEST_LINE}</p>
        {!done ? (
          <label className="block">
            <span className="mb-1 block font-medium text-foreground">Release notes (optional)</span>
            <textarea value={notes} maxLength={MAX_NOTES} rows={3} onChange={(e) => setNotes(e.target.value)} disabled={busy}
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs" />
            <span className="text-muted-foreground">{notes.length} of {MAX_NOTES} characters</span>
          </label>
        ) : null}
        {done ? (
          <div role="status" className="space-y-2 text-foreground">
            <p>{publishedWords(done)}</p>
            <Button size="sm" icon="ExternalLink" href={previewHref(pkg)}>Open the app</Button>
          </div>
        ) : null}
        {result && result.kind !== "published" ? (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-destructive">
            <span className="min-w-0 break-words">{result.message}</span>
            {result.kind === "stale" || result.kind === "unknown" ? (
              <Button size="sm" variant="secondary" icon="RefreshCw" onClick={() => { p.onReload(); p.onClose(); }}>Reload</Button>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-border px-3 py-2">
        <Button size="sm" variant="secondary" onClick={p.onClose} disabled={busy}>{done ? "Close" : "Cancel"}</Button>
        {!done ? <Button size="sm" icon="Rocket" loading={busy} disabled={!canPublish} onClick={send}>Publish version {next}</Button> : null}
      </div>
    </Modal>
  );
}
