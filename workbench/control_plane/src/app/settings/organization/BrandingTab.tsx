"use client";

/**
 * Organisation → Branding — the customer's own identity inside the product.
 *
 * A TAB of the Organisation surface since D49 (`launch_surface.md` §6.2), not a
 * page of its own; the admin gate and the org scoping are unchanged, and the
 * parent already refuses a non-admin before this renders.
 *
 * Today that is one thing: the logo that replaces our mark in the top-left of
 * every member's shell, with "powered by Metorite" beneath it. The page is
 * scoped to the org, so it is admin-gated — one member changing what the whole
 * company sees is exactly the kind of write that has an admin gate on it.
 *
 * The gateway is the authority on whether an upload is acceptable
 * (`gateway/routes/settings.py`); the pre-check here only spares an admin a
 * round-trip for a file that obviously will not do.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { useAccess } from "@/components/AccessProvider";
import Button from "@/components/ui/Button";
import { BrandMark, invalidateOrgBranding } from "@/components/OrgBrandLockup";
import {
  LOGO_ACCEPT,
  LOGO_RULES,
  POWERED_BY,
  type OrgBranding,
  formatBytes,
  precheckLogoFile,
} from "@/lib/orgBranding";

export default function BrandingTab() {
  const { access } = useAccess();
  const [branding, setBranding] = useState<OrgBranding | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"upload" | "remove" | null>(null);
  const [error, setError] = useState("");
  const [pickedName, setPickedName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/settings/branding", { cache: "no-store" });
      if (!r.ok) throw new Error(`Could not load branding (${r.status})`);
      setBranding((await r.json()) as OrgBranding);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load branding.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Read the file as base64 without the `data:…;base64,` prefix. */
  const encode = (file: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("That file could not be read."));
      reader.onload = () => {
        const result = String(reader.result ?? "");
        // The server strips a prefix too, but sending only the payload keeps
        // the caller-declared MIME type out of the request entirely.
        resolve(result.slice(result.indexOf(",") + 1));
      };
      reader.readAsDataURL(file);
    });

  const onPick = async (file: File | undefined) => {
    // Always clear the input's value: picking the same file twice in a row
    // fires no change event otherwise, so a failed upload could not be retried.
    if (inputRef.current) inputRef.current.value = "";
    if (!file) return;

    setPickedName(file.name);
    const complaint = precheckLogoFile(file);
    if (complaint) {
      setError(complaint);
      return;
    }

    setBusy("upload");
    setError("");
    try {
      const r = await fetch("/api/settings/branding", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ logoBase64: await encode(file) }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail ?? `Upload failed (${r.status})`);
      setBranding(body as OrgBranding);
      // Push the new mark into the shell so it changes now, rather than at the
      // next full page load — the whole point of this page is seeing it work.
      invalidateOrgBranding(body as OrgBranding);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(null);
    }
  };

  const onRemove = async () => {
    setBusy("remove");
    setError("");
    try {
      const r = await fetch("/api/settings/branding", { method: "DELETE" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail ?? `Could not remove (${r.status})`);
      setBranding(body as OrgBranding);
      setPickedName("");
      invalidateOrgBranding(body as OrgBranding);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove the logo.");
    } finally {
      setBusy(null);
    }
  };

  if (!access?.is_admin) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">
          Organization settings are admin-only.
        </p>
      </div>
    );
  }

  const logo = branding?.logo ?? null;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-border px-4 py-3 shrink-0 sm:px-6 sm:py-4">
        <Link
          href="/settings/models"
          className="rounded-lg border border-border p-2 text-muted-foreground tech-transition hover:bg-secondary"
          aria-label="Back to settings"
        >
          <Icon name="ArrowLeft" size={15} />
        </Link>
        <div>
          <h1 className="text-base font-bold text-foreground sm:text-lg">Organization</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            How your company appears to everyone in it
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <section className="max-w-2xl rounded-xl border border-border p-4 sm:p-5">
          <h2 className="text-sm font-semibold text-foreground">Logo</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Shown in the top-left corner of the app for every member of your
            organization, above “{POWERED_BY}”.
          </p>

          {/* The preview sits on the sidebar's own background, because that is
              where it will actually live — a logo checked against the page
              background is how a dark wordmark ships onto a dark rail. */}
          <div className="mt-4 flex flex-wrap items-center gap-4">
            {/* Width-matched to the sidebar's own lockup slot (w-64 rail, less
                px-4 padding and the collapse control), so the preview clips
                where the real thing clips. A preview in a roomier box is how a
                wordmark that truncates in the rail looks fine here. */}
            <div className="flex h-20 w-[184px] items-center rounded-lg border border-sidebar-border bg-sidebar px-4">
              {loading ? (
                <span className="text-xs text-muted-foreground">Loading…</span>
              ) : (
                // The shell's own component, not a copy of it. The copy that
                // used to be here rendered the logo and the attribution side by
                // side — which is not what the sidebar does.
                <BrandMark
                  branding={branding}
                  fallbackCaption="No logo uploaded"
                />
              )}
            </div>

            <div className="flex flex-col gap-2">
              {/* AGENTS.md rule 3: the native input is hidden and driven by a
                  themed button. The browser's own "Choose File / No file
                  chosen" control cannot be themed and would be the one piece of
                  unstyled chrome in the product. */}
              <input
                ref={inputRef}
                type="file"
                accept={LOGO_ACCEPT}
                className="hidden"
                onChange={(e) => void onPick(e.target.files?.[0])}
              />
              <Button
                size="sm"
                layout="flex items-center"
                disabled={busy !== null}
                onClick={() => inputRef.current?.click()}
              >
                <Icon name="Upload" size={14} />
                {busy === "upload"
                  ? "Uploading…"
                  : logo
                    ? "Replace logo"
                    : "Upload logo"}
              </Button>
              {logo ? (
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => void onRemove()}
                >
                  {busy === "remove" ? "Removing…" : "Remove"}
                </Button>
              ) : null}
              {/* The app names the chosen file, because hiding the native input
                  also hides the only place a browser would have said so. */}
              {pickedName ? (
                <p className="max-w-[12rem] truncate text-[11px] text-muted-foreground">
                  {pickedName}
                </p>
              ) : null}
            </div>
          </div>

          {error ? (
            <p className="mt-4 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <ul className="mt-4 flex flex-col gap-1">
            {LOGO_RULES.map((rule) => (
              <li
                key={rule}
                className="flex items-start gap-2 text-xs text-muted-foreground"
              >
                <Icon name="Check" size={12} className="mt-0.5 shrink-0" />
                {rule}
              </li>
            ))}
          </ul>

          {logo ? (
            <p className="mt-4 text-[11px] text-muted-foreground">
              Current: {logo.width}×{logo.height} · {formatBytes(logo.byteSize)}
              {branding?.updatedBy ? ` · set by ${branding.updatedBy}` : ""}
            </p>
          ) : null}
        </section>
      </div>
    </div>
  );
}
