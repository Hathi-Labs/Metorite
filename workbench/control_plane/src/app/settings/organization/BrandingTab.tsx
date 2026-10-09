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
 * (`gateway/routes/settings.py`). Since 2026-10-09 an admin rarely meets its
 * rules: a picked file opens in the logo editor (`LogoEditor.tsx`), which
 * crops it and draws the small PNG the shell needs, with a dark-mode version.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { useAccess } from "@/components/AccessProvider";
import Button from "@/components/ui/Button";
import { BrandMark, invalidateOrgBranding } from "@/components/OrgBrandLockup";
import { LOGO_PICK_ACCEPT, type Source, decodeFile } from "@/lib/logoCanvas";
import {
  LOGO_RULES,
  POWERED_BY,
  type OrgBranding,
  formatBytes,
  precheckLogoFile,
} from "@/lib/orgBranding";
import SettingsHeader from "@/components/SettingsHeader";
import type { DarkStyle } from "@/lib/logoImage";
import LogoEditor, { type LogoSave, type OwnDark, modeVars } from "./LogoEditor";

/** Decode a stored logo, so the editor can open what is saved today. */
async function decodeStored(dataUri: string, name: string): Promise<Source> {
  const blob = await (await fetch(dataUri)).blob();
  return decodeFile(new File([blob], name, { type: blob.type }));
}

export default function BrandingTab() {
  const { access } = useAccess();
  const [branding, setBranding] = useState<OrgBranding | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"upload" | "remove" | null>(null);
  const [error, setError] = useState("");
  const [pickedName, setPickedName] = useState("");
  const [editing, setEditing] = useState<Source | null>(null);
  // A fresh editor for every picked file. Reused, it kept the previous
  // image's crop box, which can lie outside the new image and draw nothing
  // (measured 2026-10-09, a long wordmark after a square logo).
  const [editKey, setEditKey] = useState(0);
  const [editError, setEditError] = useState("");
  // Set when the editor reopens the saved logo ("Change dark mode").
  const [editInitial, setEditInitial] = useState<{ style: DarkStyle; own: OwnDark | null } | null>(null);
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

  /** Open the picked file in the editor. Nothing is sent until it saves. */
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
    setError("");
    try {
      setEditError("");
      const source = await decodeFile(file);
      setEditKey((k) => k + 1);
      setEditInitial(null);
      setEditing(source);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That file could not be opened.");
    }
  };

  /** Send what the editor drew. The server still checks every bound. */
  const onSave = async (save: LogoSave) => {
    setBusy("upload");
    setEditError("");
    try {
      const r = await fetch("/api/settings/branding", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(save),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail ?? `Upload failed (${r.status})`);
      setBranding(body as OrgBranding);
      // Push the new mark into the shell so it changes now, rather than at the
      // next full page load — the whole point of this page is seeing it work.
      invalidateOrgBranding(body as OrgBranding);
      setEditing(null);
    } catch (e) {
      // Shown inside the editor, so the crop and the choices are not lost.
      setEditError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(null);
    }
  };

  /**
   * Reopen the saved logo, to add or change its dark-mode version without
   * finding the original file again. The saved dark image comes too.
   */
  const onChangeDark = async () => {
    const current = branding?.logo;
    if (!current) return;
    setError("");
    try {
      const source = await decodeStored(current.dataUri, "your current logo");
      const style: DarkStyle = branding?.darkStyle ?? "same";
      const own =
        style === "own" && branding?.logoDark
          ? { source: await decodeStored(branding.logoDark.dataUri, "your dark version"), name: "your dark version" }
          : null;
      setPickedName("your current logo");
      setEditError("");
      setEditKey((k) => k + 1);
      setEditInitial({ style, own });
      setEditing(source);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The saved logo could not be opened.");
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
      <div className="shrink-0 border-b border-border px-4 py-3 sm:px-6 sm:py-4">
        <SettingsHeader
          title="Organization"
          subtitle="How your company appears to everyone in it"
          backHref="/settings/models"
          backLabel="Back to settings"
        />
      </div>

      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <section className="max-w-2xl rounded-xl border border-border p-4 sm:p-5">
          <h2 className="text-sm font-semibold text-foreground">Logo</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Every member of your organization sees it, above “{POWERED_BY}”:
            at the top of the sidebar on a computer, and at the top of the menu
            on a phone.
          </p>

          {/* Both colour modes, side by side, each on the sidebar's own
              background, because that is where the logo lives. Width-matched
              to the sidebar's lockup slot, so the preview clips where the
              real thing clips. The shell's own component draws both. */}
          <div className="mt-4 flex flex-wrap items-start gap-4">
            {(["light", "dark"] as const).map((mode) => (
              <div key={mode} className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">{mode === "light" ? "Light mode" : "Dark mode"}</span>
                <div className="flex h-20 w-[184px] items-center rounded-lg border px-4" style={modeVars(mode)}>
                  {loading ? (
                    <span className="text-xs text-muted-foreground">Loading…</span>
                  ) : (
                    <BrandMark branding={branding} fallbackCaption="No logo uploaded" mode={mode} />
                  )}
                </div>
              </div>
            ))}

            <div className="flex flex-col gap-2">
              {/* AGENTS.md rule 3: the native input is hidden and driven by a
                  themed button. The browser's own "Choose File / No file
                  chosen" control cannot be themed and would be the one piece of
                  unstyled chrome in the product. */}
              <input
                ref={inputRef}
                type="file"
                accept={LOGO_PICK_ACCEPT}
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
                {logo ? "Replace logo" : "Upload logo"}
              </Button>
              {logo ? (
                <Button
                  variant="secondary"
                  size="sm"
                  icon="Moon"
                  disabled={busy !== null}
                  onClick={() => void onChangeDark()}
                >
                  Change dark mode
                </Button>
              ) : null}
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
        {editing ? (
          <LogoEditor
            key={editKey}
            source={editing}
            fileName={pickedName}
            saving={busy === "upload"}
            error={editError}
            onCancel={() => setEditing(null)}
            onSave={(save) => void onSave(save)}
            initialStyle={editInitial?.style ?? null}
            initialOwn={editInitial?.own ?? null}
          />
        ) : null}
      </div>
    </div>
  );
}
