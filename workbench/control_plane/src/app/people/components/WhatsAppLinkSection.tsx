"use client";

/**
 * Chat on WhatsApp, on My Profile (WS-47 WAC-1).
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §5.2.
 *
 * The member asks for a link. The gateway issues a single-use code that
 * works for 15 minutes, and answers with a `wa.me` link that opens WhatsApp
 * with "Link me: <code>" ready to send. A desktop member scans the QR code of
 * the same link with their phone. The message proves they hold the phone.
 *
 * Every decision is in `../lib/whatsappLink.ts`, where vitest can reach it.
 * This file draws. ⚠️ It draws NOTHING when `sectionView` says `hidden`,
 * which is every answer but a 200 with `enabled: true`. The channel ships
 * dark, so a member on a box with the switch off never sees it.
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";

import {
  type IssuedCode,
  type SectionView,
  clockTime,
  fetchLinkState,
  issueCode,
  qrDataUrl,
  sectionView,
} from "../lib/whatsappLink";

export function WhatsAppLinkSection() {
  const [view, setView] = useState<SectionView>({ kind: "hidden" });
  const [issued, setIssued] = useState<IssuedCode | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setView(sectionView(await fetchLinkState()));
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    let live = true;
    if (!issued) {
      setQr(null);
      return;
    }
    qrDataUrl(issued.link)
      .then((url) => {
        if (live) setQr(url);
      })
      .catch(() => {
        if (live) setQr(null);
      });
    return () => {
      live = false;
    };
  }, [issued]);

  if (view.kind === "hidden") return null;

  async function onGetLink() {
    setBusy(true);
    setError(null);
    try {
      setIssued(await issueCode());
      await reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-border p-3">
      <div className="flex items-center gap-2 text-xs font-medium text-foreground">
        <Icon name="MessageCircle" className="h-4 w-4" />
        Chat on WhatsApp
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Ask Metorite about your tasks, projects and calendar from your own
        WhatsApp. Get a link, open it on your phone, and send the message it
        fills in.
      </p>

      {view.links.length > 0 && (
        <ul className="mt-2 space-y-1">
          {view.links.map((row) => (
            <li key={row.key} className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone={row.tone} size="xs">
                {row.label}
              </Badge>
              <span className="text-muted-foreground">{row.detail}</span>
            </li>
          ))}
        </ul>
      )}

      {issued && (
        <div className="mt-3 flex flex-wrap items-start gap-4">
          {qr ? (
            // A QR code is a data image that a camera reads. It must stay
            // dark on light in every colour mode, so it is a PNG and not a
            // themed drawing.
            // eslint-disable-next-line @next/next/no-img-element -- a data URI has
            // nothing to optimise: it is already inline.
            <img
              src={qr}
              alt="QR code of your WhatsApp link"
              width={160}
              height={160}
              className="rounded-md border border-border"
            />
          ) : (
            <div className="h-40 w-40 rounded-md border border-border" />
          )}
          <div className="min-w-0 flex-1 space-y-2">
            <p className="text-[11px] text-muted-foreground">
              On a computer, scan the code with your phone. On your phone, open
              the link. The link works once, until{" "}
              {clockTime(issued.expires_at)} ({issued.code_ttl_minutes} minutes).
            </p>
            <a
              href={issued.link}
              target="_blank"
              rel="noopener noreferrer"
              className="block break-all text-xs text-foreground hover:underline"
            >
              {issued.link}
            </a>
            <Button
              size="sm"
              icon="ExternalLink"
              onClick={() =>
                window.open(issued.link, "_blank", "noopener,noreferrer")
              }
            >
              Open WhatsApp
            </Button>
          </div>
        </div>
      )}

      <div className="mt-3 flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          icon="Link"
          loading={busy}
          onClick={() => void onGetLink()}
        >
          {issued ? "Get a new link" : "Get my link"}
        </Button>
        {error && (
          <span className="text-[11px] text-destructive" role="alert">
            {error}
          </span>
        )}
      </div>
    </section>
  );
}
