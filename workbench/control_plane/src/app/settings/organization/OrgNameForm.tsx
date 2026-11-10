"use client";

/**
 * Organisation → Branding → Name — the company's display name.
 *
 * `PATCH /api/admin/organization` is the one write. The gateway trims,
 * bounds and gates it (`admin:settings:manage`); the checks here only spare an
 * admin a round-trip. After a save the shared access snapshot is refreshed, so
 * the Organisation header and every other surface that reads
 * `access.organization.display_name` change now, not at the next page load.
 */

import { useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export const NAME_MAX = 80;

/** What the admin typed, as the gateway will store it. */
export function normaliseName(raw: string): string {
  return raw.replace(/\s+/g, " ").trim();
}

/** `null` when the name may be saved, otherwise the sentence to show. */
export function nameProblem(raw: string): string | null {
  const name = normaliseName(raw);
  if (!name) return "The organization name cannot be empty.";
  if (name.length > NAME_MAX) {
    return `The organization name is ${name.length} characters; the limit is ${NAME_MAX}.`;
  }
  return null;
}

export default function OrgNameForm() {
  const { access, refresh } = useAccess();
  const current = access.organization?.display_name ?? "";
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const value = draft ?? current;
  const problem = draft === null ? null : nameProblem(draft);
  const dirty = normaliseName(value) !== current;

  const save = async () => {
    const complaint = nameProblem(value);
    if (complaint) {
      setError(complaint);
      return;
    }
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const r = await fetch("/api/admin/organization", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: normaliseName(value) }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(
          typeof body.detail === "string" ? body.detail : `Could not save the name (${r.status})`,
        );
      }
      await refresh();
      setDraft(null);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the name.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="max-w-2xl rounded-xl border border-border p-4 sm:p-5">
      <h2 className="text-sm font-semibold text-foreground">Name</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        The name your members see for the organization. It does not change the
        sign-in domain or any link.
      </p>

      <form
        className="mt-4 flex flex-wrap items-start gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty && !problem && !busy) void save();
        }}
      >
        <Input
          aria-label="Organization name"
          className="min-w-0 flex-1 basis-64"
          value={value}
          maxLength={NAME_MAX + 20}
          disabled={busy}
          onChange={(e) => {
            setDraft(e.target.value);
            setSaved(false);
            setError("");
          }}
        />
        <Button type="submit" size="sm" disabled={busy || !dirty || Boolean(problem)}>
          {busy ? "Saving…" : "Save name"}
        </Button>
      </form>

      {problem && draft !== null ? (
        <p className="mt-2 text-xs text-destructive">{problem}</p>
      ) : null}
      {error ? (
        <p className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {saved ? (
        <p className="mt-2 text-xs text-success" role="status">
          Name saved.
        </p>
      ) : null}
    </section>
  );
}
