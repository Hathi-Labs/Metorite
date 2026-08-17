"use client";

/**
 * /settings/appearance — pick the look of the Control Plane.
 *
 * Two scopes, deliberately separated in the UI because they behave differently:
 *
 *   • Your appearance — theme, mode, density and accent for this browser.
 *     Applies the moment you click; nothing to save.
 *   • Organisation default — what everyone gets who has not chosen for
 *     themselves. Admin-only, and needs a gateway that stores it.
 *
 * Each theme card renders a real preview built from that theme's own tokens
 * (its surfaces, its primary, its radius, its font), rather than a static
 * screenshot, so the gallery cannot go stale when a manifest changes.
 */

import { useCallback, useEffect, useState } from "react";
import { useTheme } from "next-themes";
import Icon from "@/components/Icon";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { THEMES, resolveTheme } from "@/lib/theme/themes";
import { useAppearanceStore, effectiveThemeId, effectiveDensity } from "@/lib/theme/store";
import { isSafeColor } from "@/lib/theme/css";
import type { AppearanceSettings, ColorTokens, Density, Theme, ThemeMode } from "@/lib/theme/types";
import { DENSITY_SCALE } from "@/lib/theme/types";

const DENSITY_LABELS: Record<Density, string> = {
  compact: "Compact",
  default: "Default",
  comfortable: "Comfortable",
};

/** Accent suggestions — one per built-in theme's primary, plus a few extras. */
const ACCENT_PRESETS = [
  { label: "Theme default", value: null },
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
          Themes, colour mode, density and accent across the Control Plane
        </p>
      </div>
    </div>
  );
}

// ── Personal ────────────────────────────────────────────────────────────────

function PersonalSettings() {
  const { theme: mode, setTheme: setMode } = useTheme();
  const activeId = useAppearanceStore(effectiveThemeId);
  const density = useAppearanceStore(effectiveDensity);
  const accent = useAppearanceStore((s) => s.accent);
  const userThemeId = useAppearanceStore((s) => s.userThemeId);
  const orgThemeId = useAppearanceStore((s) => s.orgThemeId);
  const allowUserOverride = useAppearanceStore((s) => s.allowUserOverride);
  const setUserTheme = useAppearanceStore((s) => s.setUserTheme);
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
          Everyone is on <span className="text-foreground">{resolveTheme(orgThemeId).name}</span>. An
          administrator can allow personal overrides from the Organisation default tab.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <section>
        <SectionHeading
          title="Theme"
          description="Changes colours, fonts, corner radius, effects and the icon pack across every page."
        />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {THEMES.map((theme) => (
            <ThemeCard
              key={theme.id}
              theme={theme}
              mode={previewMode}
              selected={activeId === theme.id}
              isOrgDefault={orgThemeId === theme.id}
              onSelect={() => setUserTheme(theme.id)}
            />
          ))}
        </div>
        {userThemeId && (
          <Button variant="secondary" size="lg" className="mt-3" onClick={() => setUserTheme(null)}>
            Reset to organisation default ({resolveTheme(orgThemeId).name})
          </Button>
        )}
      </section>

      <section>
        <SectionHeading
          title="Colour mode"
          description="Every theme ships both. Independent of your theme choice."
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
          description="Overrides the theme's primary colour. Leave on Theme default unless you have a reason."
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
          title="Default theme"
          description="What members see before they choose one for themselves."
        />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {THEMES.map((theme) => (
            <ThemeCard
              key={theme.id}
              theme={theme}
              mode={settings.org.mode}
              selected={settings.org.themeId === theme.id}
              onSelect={() => save({ themeId: theme.id })}
              disabled={saving || !settings.orgManaged}
            />
          ))}
        </div>
      </section>

      <section>
        <SectionHeading title="Default colour mode" description="" />
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
          description="Turn off to standardise the whole company on the default theme."
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

/**
 * A theme card whose preview is drawn with the theme's own tokens.
 *
 * The swatch is a miniature of the real UI — surface, card, a primary button,
 * a muted line — rendered with inline custom properties rather than Tailwind
 * classes, because the classes resolve against the ACTIVE theme and would show
 * every card in the same colours.
 */
function ThemeCard({
  theme,
  mode,
  selected,
  isOrgDefault,
  onSelect,
  disabled,
}: {
  theme: Theme;
  mode: ThemeMode;
  selected: boolean;
  isOrgDefault?: boolean;
  onSelect: () => void;
  disabled?: boolean;
}) {
  const c: ColorTokens = theme.colors[mode];

  return (
    <button
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`w-full rounded-xl border p-3 text-left tech-transition disabled:cursor-not-allowed disabled:opacity-50 sm:p-4 ${
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary/20"
          : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
      }`}
    >
      <div
        className="mb-3 overflow-hidden rounded-lg border"
        style={{
          background: c.background,
          borderColor: c.border,
          borderRadius: theme.shape.radius,
          fontFamily: theme.typography.app,
        }}
      >
        <div className="flex items-center gap-1.5 px-2.5 py-2" style={{ background: c.sidebarBackground ?? c.background }}>
          <span
            className="inline-block h-3.5 w-3.5"
            style={{ background: c.primary, borderRadius: theme.shape.radius }}
          />
          <span className="text-[10px] font-semibold" style={{ color: c.foreground }}>
            Metorite
          </span>
        </div>
        <div className="space-y-1.5 p-2.5">
          <div
            className="space-y-1.5 p-2"
            style={{
              background: c.card,
              borderRadius: theme.shape.radius,
              border: `${theme.shape.borderWidth} solid ${c.border}`,
            }}
          >
            <div className="h-1.5 w-2/3 rounded-full" style={{ background: c.foreground, opacity: 0.85 }} />
            <div className="h-1.5 w-full rounded-full" style={{ background: c.mutedForeground, opacity: 0.5 }} />
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className="px-2 py-1 text-[9px] font-medium"
              style={{
                background: c.primary,
                color: c.primaryForeground,
                borderRadius: theme.shape.radius,
                fontWeight: theme.typography.labelWeight,
              }}
            >
              Action
            </span>
            <span
              className="px-2 py-1 text-[9px]"
              style={{
                color: c.mutedForeground,
                borderRadius: theme.shape.radius,
                border: `${theme.shape.borderWidth} solid ${c.border}`,
              }}
            >
              Cancel
            </span>
            <span className="ml-auto flex gap-1">
              {[c.success, c.warning, c.destructive, c.accent].map((color, i) => (
                <span key={i} className="h-2 w-2 rounded-full" style={{ background: color }} />
              ))}
            </span>
          </div>
        </div>
      </div>

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold text-foreground">{theme.name}</span>
            {selected && <Icon name="Check" size={13} className="shrink-0 text-primary" />}
          </div>
          <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{theme.description}</p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge>{theme.iconPack} icons</Badge>
        <Badge>{theme.shape.radius === "0.25rem" ? "square" : theme.shape.radius === "1rem" ? "rounded" : "radius " + theme.shape.radius}</Badge>
        {isOrgDefault && <Badge>org default</Badge>}
      </div>
    </button>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-md bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
      {children}
    </span>
  );
}
