"use client";

/**
 * "Number format": lakh and crore, or million and billion (owner, 2026-10-19).
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §13.10.
 *
 *   • `OrgNumberFormat` — Settings → Organisation → Branding. An admin sets
 *     the organization's value. The gateway refuses anyone else.
 *   • `MyNumberFormat` — Appearance → Your appearance. A member sets their
 *     own, or "Use the organization's setting".
 *
 * A change saves at once, as the other choices on those pages do. The gateway
 * resolves the member's system, and a save hands it to every chart on the
 * page (`lib/numberSystem.ts`). The charts, and the AI's words in the chat and
 * on WhatsApp, follow it.
 */

import { useEffect, useState } from "react";

import { SelectButton } from "@/components/ui/SelectButton";
import {
  NUMBER_SYSTEM_LABELS,
  type NumberSettings,
  type NumberSystem,
  USE_ORG,
  fetchNumberSettings,
  memberChoice,
  memberOptions,
  memberValue,
  saveMyNumberSystem,
  saveOrgNumberSystem,
} from "@/lib/numberSystem";
import { DEFAULT_NUMBER_SYSTEM } from "@/lib/numberFormat.mjs";

const SYSTEMS: NumberSystem[] = ["indian", "international"];

/** The settings, a save, and what to say about either. */
function useNumberSettings() {
  const [settings, setSettings] = useState<NumberSettings | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchNumberSettings()
      .then((s) => alive && setSettings(s))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : "Could not load the number format."));
    return () => {
      alive = false;
    };
  }, []);

  const run = async (save: () => Promise<NumberSettings>) => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      setSettings(await save());
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the number format.");
    } finally {
      setBusy(false);
    }
  };
  return { settings, error, busy, saved, run };
}

function Status({ error, saved }: { error: string; saved: boolean }) {
  if (error) {
    return (
      <p className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
        {error}
      </p>
    );
  }
  return saved ? (
    <p className="mt-2 text-xs text-success" role="status">
      Number format saved.
    </p>
  ) : null;
}

/** The organization's number format, for an admin. */
export function OrgNumberFormat() {
  const { settings, error, busy, saved, run } = useNumberSettings();
  const value = settings?.org ?? DEFAULT_NUMBER_SYSTEM;
  return (
    <section className="max-w-2xl rounded-xl border border-border p-4 sm:p-5">
      <h2 className="text-sm font-semibold text-foreground">Number format</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        How charts and the AI write large numbers for your members. A member
        can choose their own in Appearance.
      </p>
      <div className="mt-4">
        <SelectButton
          label="Organization number format"
          value={value}
          options={SYSTEMS.map((s) => ({ value: s, label: NUMBER_SYSTEM_LABELS[s] }))}
          defaultValue={DEFAULT_NUMBER_SYSTEM}
          widthClass="w-72 max-w-full"
          disabled={busy || settings === null}
          onChange={(next) => void run(() => saveOrgNumberSystem(next as NumberSystem))}
        />
      </div>
      <Status error={error} saved={saved} />
    </section>
  );
}

/** The member's own number format. */
export function MyNumberFormat() {
  const { settings, error, busy, saved, run } = useNumberSettings();
  return (
    <section>
      <div className="mb-3">
        <h2 className="text-sm font-semibold text-foreground">Number format</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          How charts and the AI write large numbers for you.
        </p>
      </div>
      <SelectButton
        label="My number format"
        value={settings ? memberChoice(settings) : USE_ORG}
        options={memberOptions(settings)}
        defaultValue={USE_ORG}
        widthClass="w-80 max-w-full"
        disabled={busy || settings === null}
        onChange={(next) => void run(() => saveMyNumberSystem(memberValue(next)))}
      />
      <Status error={error} saved={saved} />
    </section>
  );
}
