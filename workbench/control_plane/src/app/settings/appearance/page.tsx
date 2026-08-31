"use client";

/**
 * /settings/appearance — the few things you may change about the look.
 *
 * ⚠️ **There is no theme picker** (owner directive 2026-08-31). The Control
 * Plane has ONE look, carried by `globals.css`, and every app and sub-app
 * renders in it. What remains here adjusts that look rather than replacing
 * it: colour mode, density and accent. A gallery of themes is exactly what
 * this page used to be, and exactly what was retired.
 *
 * Two scopes, deliberately separated because they behave differently:
 *
 *   • Your appearance — mode, density and accent for this browser. Applies
 *     the moment you click; nothing to save.
 *   • Organisation default — what everyone gets who has not chosen for
 *     themselves. Admin-only, and needs a gateway that stores it.
 */

import { useCallback, useEffect, useState } from "react";
import { useTheme } from "next-themes";
import Icon from "@/components/Icon";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAppearanceStore, effectiveDensity } from "@/lib/theme/store";
import { isSafeColor } from "@/lib/theme/css";
import type { AppearanceSettings, Density, ThemeMode } from "@/lib/theme/types";
import { DENSITY_SCALE } from "@/lib/theme/types";

const DENSITY_LABELS: Record<Density, string> = {
  compact: "Compact",
  default: "Default",
  comfortable: "Comfortable",
};

/** Accent suggestions. `null` restores the app's own blue. */
const ACCENT_PRESETS = [
  { label: "Default", value: null },
  { label: "Azure", value: "hsl(206 100% 42%)" },
  { label: "Violet", value: "hsl(258 60% 55%)" },
  { label: "Emerald", value: "hsl(158 64% 38%)" },
  { label: "Amber", value: "hsl(32 95% 48%)" },
  { label: "Rose", value: "hsl(347 77% 50%)" },
];

export default function AppearancePage() {
  const [tab, setTab] = useState<"personal" | "organisation">("personal");

  // Preferences live in localStorage, so the server cannot know them. Waiting
  // on the store's own hydration flag — rather than a bare mounted flag —
  // gates the render on the preferences actually being loaded, and keeps the
  // server's markup and the first client paint identical.
  const hydrated = useAppearanceStore((s) => s.hydrated);

  if (!hydrated) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader />
        <div className="flex-1 p-4 text-xs text-muted-foreground">Loading preferences…</div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader />
      <Tabs
        tabs={[
          { id: "personal", label: "Your appearance" },
          { id: "organisation", label: "Organisation default" },
        ]}
        activeTab={tab}
        onTabChange={(id) => setTab(id as typeof tab)}
        variant="underline"
      />
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        {tab === "personal" ? <PersonalSettings /> : <OrganisationSettings />}
      </div>
    </div>
  );
}

function PageHeader() {
  return (
    <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3 sm:px-6 sm:py-4">
      <div>
        <h1 className="text-base font-bold text-foreground sm:text-lg">Appearance</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Colour mode, density and accent across the Control Plane
        </p>
      </div>
    </div>
  );
}

// ── Personal ────────────────────────────────────────────────────────────────

function PersonalSettings() {
  const { theme: mode, setTheme: setMode } = useTheme();
  const density = useAppearanceStore(effectiveDensity);
  const accent = useAppearanceStore((s) => s.accent);
  const allowUserOverride = useAppearanceStore((s) => s.allowUserOverride);
  const setUserDensity = useAppearanceStore((s) => s.setUserDensity);
  const setAccent = useAppearanceStore((s) => s.setAccent);

  const previewMode: ThemeMode = mode === "light" ? "light" : "dark";

  if (!allowUserOverride) {
    return (
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Icon name="Lock" size={15} />
          Appearance is managed by your organisation
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          An administrator can allow personal overrides from the Organisation default
          tab.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <section>
        <SectionHeading
          title="Colour mode"
          description="The same look, lit two ways."
        />
        <div className="flex gap-2">
          {(["dark", "light"] as const).map((m) => (
            <OptionPill
              key={m}
              selected={previewMode === m}
              onClick={() => setMode(m)}
              icon={m === "dark" ? "Moon" : "Sun"}
              label={m === "dark" ? "Dark" : "Light"}
            />
          ))}
        </div>
      </section>

      <section>
        <SectionHeading
          title="Density"
          description="Scales text and spacing together. Useful on very large or very small screens."
        />
        <div className="flex flex-wrap gap-2">
          {(Object.keys(DENSITY_SCALE) as Density[]).map((d) => (
            <OptionPill
              key={d}
              selected={density === d}
              onClick={() => setUserDensity(d)}
              label={DENSITY_LABELS[d]}
              hint={`${Math.round(DENSITY_SCALE[d] * 100)}%`}
            />
          ))}
        </div>
      </section>

      <section>
        <SectionHeading
          title="Accent colour"
          description="Overrides the primary colour. Leave on Default unless you have a reason."
        />
        <div className="flex flex-wrap gap-2">
          {ACCENT_PRESETS.map((preset) => (
            <button
              key={preset.label}
              onClick={() => setAccent(preset.value)}
              className={`cc-control flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
                accent === preset.value
                  ? "border-primary bg-primary/5 text-foreground ring-1 ring-primary/20"
                  : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
              }`}
            >
              <span
                className="h-3.5 w-3.5 rounded-full border border-border"
                style={{ background: preset.value ?? "var(--primary)" }}
              />
              {preset.label}
            </button>
          ))}
        </div>
        <CustomAccentInput value={accent} onChange={setAccent} />
      </section>
    </div>
  );
}

function CustomAccentInput({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(() => {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError(null);
      onChange(null);
      return;
    }
    if (!isSafeColor(trimmed)) {
      setError("Use a plain colour, e.g. #0078d4 or hsl(206 100% 42%)");
      return;
    }
    setError(null);
    onChange(trimmed);
  }, [draft, onChange]);

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="w-48">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && apply()}
            placeholder="#0078d4"
            aria-label="Custom accent colour"
            inputSize="lg"
          />
        </div>
        <Button variant="secondary" size="lg" onClick={apply}>
          Apply
        </Button>
      </div>
      {error && <p className="mt-1.5 text-[11px] text-destructive">{error}</p>}
    </div>
  );
}

// ── Organisation ────────────────────────────────────────────────────────────

function OrganisationSettings() {
  const [settings, setSettings] = useState<AppearanceSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const setOrgDefaults = useAppearanceStore((s) => s.setOrgDefaults);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings/appearance")
      .then((r) => (r.ok ? r.json() : null))
      .then((data: AppearanceSettings | null) => {
        if (!cancelled && data) setSettings(data);
      })
      .catch(() => {
        if (!cancelled) setMessage({ kind: "error", text: "Could not load organisation defaults." });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback(
    async (patch: Partial<AppearanceSettings["org"]>) => {
      if (!settings) return;
      const next = { ...settings.org, ...patch };
      setSaving(true);
      setMessage(null);
      try {
        const res = await fetch("/api/settings/appearance", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(next),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setMessage({
            kind: "error",
            text:
              res.status === 403
                ? "Only an administrator can change the organisation default."
                : (body?.error ?? body?.detail ?? `Could not save (${res.status}).`),
          });
          return;
        }
        const saved = await res.json().catch(() => null);
        setSettings({
          ...settings,
          org: next,
          updatedBy: saved?.updatedBy ?? settings.updatedBy,
          updatedAt: saved?.updatedAt ?? settings.updatedAt,
        });
        // Push it into the live store too, so the admin sees the effect
        // immediately rather than on the next reload.
        setOrgDefaults(next);
        setMessage({ kind: "ok", text: "Organisation default updated." });
      } catch {
        setMessage({ kind: "error", text: "Could not reach the server." });
      } finally {
        setSaving(false);
      }
    },
    [settings, setOrgDefaults],
  );

  if (!settings) {
    return <p className="text-xs text-muted-foreground">Loading organisation defaults…</p>;
  }

  return (
    <div className="space-y-8">
      {!settings.orgManaged && (
        <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3">
          <Icon name="AlertTriangle" size={15} className="mt-0.5 shrink-0 text-warning" />
          <div className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">
              No appearance store is configured on the gateway.
            </span>{" "}
            The values below are the built-in defaults and cannot be saved yet. Personal
            preferences on the other tab work regardless.
          </div>
        </div>
      )}

      <section>
        <SectionHeading
          title="Default colour mode"
          description="What members see before they choose for themselves."
        />
        <div className="flex gap-2">
          {(["dark", "light"] as const).map((m) => (
            <OptionPill
              key={m}
              selected={settings.org.mode === m}
              onClick={() => save({ mode: m })}
              icon={m === "dark" ? "Moon" : "Sun"}
              label={m === "dark" ? "Dark" : "Light"}
              disabled={saving || !settings.orgManaged}
            />
          ))}
        </div>
      </section>

      <section>
        <SectionHeading
          title="Personal overrides"
          description="Turn off to standardise the whole company on the defaults above."
        />
        <OptionPill
          selected={settings.org.allowUserOverride}
          onClick={() => save({ allowUserOverride: !settings.org.allowUserOverride })}
          icon={settings.org.allowUserOverride ? "Check" : "X"}
          label={settings.org.allowUserOverride ? "Members may choose" : "Locked to default"}
          disabled={saving || !settings.orgManaged}
        />
      </section>

      {message && (
        <p
          className={`text-xs ${message.kind === "ok" ? "text-success" : "text-destructive"}`}
          role="status"
        >
          {message.text}
        </p>
      )}

      {settings.orgManaged && settings.updatedBy && (
        <p className="text-[11px] text-muted-foreground">
          Last changed by {settings.updatedBy}
          {settings.updatedAt && ` on ${new Date(settings.updatedAt).toLocaleString()}`}.
        </p>
      )}
    </div>
  );
}

// ── Shared pieces ───────────────────────────────────────────────────────────

function SectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}

function OptionPill({
  selected,
  onClick,
  label,
  icon,
  hint,
  disabled,
}: {
  selected: boolean;
  onClick: () => void;
  label: string;
  icon?: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`cc-control flex items-center gap-2 rounded-lg border px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50 ${
        selected
          ? "border-primary bg-primary/5 text-foreground ring-1 ring-primary/20"
          : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
      }`}
    >
      {icon && <Icon name={icon} size={14} />}
      {label}
      {hint && <span className="text-[10px] text-muted-foreground">{hint}</span>}
    </button>
  );
}

