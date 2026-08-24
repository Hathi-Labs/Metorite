"use client";

/**
 * Organisation → Members & roles → one person's access.
 *
 * Spec: project-docs/specs/org_access_control.md §6.
 *
 * The screen the whole feature exists for: give someone a role, then add or
 * remove individual pieces on top of it. Every row shows three things — the
 * resolved outcome, where it came from, and a three-way control:
 *
 *     Inherit   use whatever the role says (no override row)
 *     Allow     grant it to this person regardless of role
 *     Deny      take it away from this person regardless of role
 *
 * Allow and Deny both beat the role. Against each other the more specific one
 * wins, which is what makes "deny all agents, allow these two" work — see the
 * resolution rule in acb_auth/permissions.py.
 *
 * "Inherit" is the default and stays visually neutral, because a screen where
 * every row looks deliberately configured hides the two rows that actually are.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAccess } from "@/components/AccessProvider";
import { explainSource, type Decision, type MemberAccess, type Role } from "../types";

type Effect = "inherit" | "allow" | "deny";

/**
 * permission → override effect. A missing key means "inherit", so the value
 * type must admit `undefined` — otherwise TypeScript narrows every lookup to
 * a set that can never be "inherit".
 */
type Overrides = Partial<Record<string, "allow" | "deny">>;

export default function MemberAccessPage() {
  const params = useParams<{ email: string }>();
  const email = decodeURIComponent(params?.email ?? "");
  const { access: myAccess, refresh: refreshMyAccess } = useAccess();

  const [data, setData] = useState<MemberAccess | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      // Fetch first: no synchronous setState inside the mount effect.
      const [a, r] = await Promise.all([
        fetch(`/api/admin/members/${encodeURIComponent(email)}/access`, {
          cache: "no-store",
        }),
        fetch("/api/admin/roles", { cache: "no-store" }),
      ]);
      setError("");
      if (!a.ok) {
        const body = await a.json().catch(() => ({}));
        throw new Error(body.detail ?? `Could not load access (${a.status})`);
      }
      const payload: MemberAccess = await a.json();
      setData(payload);
      setOverrides(
        Object.fromEntries(payload.overrides.map((o) => [o.permission, o.effect]))
      );
      setReasons(
        Object.fromEntries(payload.overrides.map((o) => [o.permission, o.reason]))
      );
      if (r.ok) setRoles(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load access.");
    } finally {
      setLoading(false);
    }
  }, [email]);

  useEffect(() => {
    // Wrapped rather than called directly: the effect must not reach a
    // setState synchronously (react-hooks/set-state-in-effect), and `load` is
    // also invoked from refresh buttons, so it stays a useCallback.
    const run = async () => {
      await load();
    };
    void run();
  }, [load]);

  const dirty = useMemo(() => {
    if (!data) return false;
    const saved = Object.fromEntries(
      data.overrides.map((o) => [o.permission, o.effect])
    );
    const keys = new Set([...Object.keys(saved), ...Object.keys(overrides)]);
    return [...keys].some((k) => saved[k] !== overrides[k]);
  }, [data, overrides]);

  const setEffect = (permission: string, effect: Effect) => {
    setOverrides((prev) => {
      const next = { ...prev };
      if (effect === "inherit") delete next[permission];
      else next[permission] = effect;
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const res = await fetch(
        `/api/admin/members/${encodeURIComponent(email)}/overrides`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            overrides: Object.entries(overrides)
              .filter(([, effect]) => effect !== undefined)
              .map(([permission, effect]) => ({
                permission,
                effect,
                reason: reasons[permission] ?? "",
              })),
          }),
        }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not save access.");
      }
      setData(await res.json());
      setNotice("Access saved. It takes effect immediately.");
      await refreshMyAccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save access.");
    } finally {
      setSaving(false);
    }
  };

  const setRole = async (slug: string) => {
    setError("");
    const res = await fetch(
      `/api/admin/members/${encodeURIComponent(email)}/roles`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roles: [slug] }),
      }
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Could not change the role.");
      return;
    }
    await load();
    await refreshMyAccess();
  };

  if (!myAccess.is_admin && !loading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-sm rounded-xl border border-border bg-card p-8 text-center">
          <Icon name="ShieldOff" size={20} className="mx-auto mb-3 text-muted-foreground" />
          <h1 className="text-base font-semibold text-foreground">Admin only</h1>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-6 text-xs text-muted-foreground">
        <Icon name="Loader2" size={14} className="animate-spin" /> Loading access…
      </div>
    );
  }

  if (!data) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">{error || "Member not found."}</p>
        <Link href="/settings/organization" className="mt-3 inline-block text-xs text-primary">
          ← Back to Organisation
        </Link>
      </div>
    );
  }

  const assignable = roles.filter((r) => r.slug !== "agent_service");

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3 shrink-0 sm:px-6 sm:py-4">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/settings/organization"
            className="rounded-lg border border-border p-2 text-muted-foreground tech-transition hover:bg-secondary"
            aria-label="Back to Organisation"
          >
            <Icon name="ArrowLeft" size={15} />
          </Link>
          <div className="min-w-0">
            <h1 className="truncate text-base font-bold text-foreground sm:text-lg">
              {data.display_name || data.email}
            </h1>
            <p className="truncate text-xs text-muted-foreground">
              {data.email} · {data.status}
            </p>
          </div>
        </div>
        <Button size="lg" layout="flex items-center" onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? <Icon name="Loader2" size={14} className="animate-spin" /> : <Icon name="Save" size={14} />}
          {dirty ? "Save changes" : "Saved"}
        </Button>
      </div>

      {(error || notice) && (
        <div
          className={`mx-4 mt-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs sm:mx-6 ${
            error
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : "border-success/30 bg-success/10 text-success"
          }`}
        >
          <span className="flex-1">{error || notice}</span>
          <button
            onClick={() => {
              setError("");
              setNotice("");
            }}
            aria-label="Dismiss"
          >
            <Icon name="X" size={13} />
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {/* Role */}
        <Section
          icon={<Icon name="Sliders" size={14} />}
          title="Role"
          subtitle="The baseline. Everything below adjusts it for this person only."
        >
          <div className="flex flex-wrap gap-2">
            {assignable.map((r) => {
              const held = data.roles.includes(r.slug);
              return (
                <button
                  key={r.slug}
                  onClick={() => void setRole(r.slug)}
                  title={r.description}
                  className={`rounded-xl border p-3 text-left tech-transition ${
                    held
                      ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                      : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
                  }`}
                >
                  <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                    {held && <Icon name="Check" size={13} className="text-primary" />}
                    {r.display_name}
                  </div>
                  <div className="mt-0.5 max-w-[220px] text-[11px] text-muted-foreground">
                    {r.description}
                  </div>
                </button>
              );
            })}
          </div>
        </Section>

        {/* Features */}
        <Section
          icon={<Icon name="LayoutGrid" size={14} />}
          title="Apps and features"
          subtitle="Which parts of Metorite this person can open."
        >
          <DecisionTable
            decisions={data.features}
            labelOf={(d) => d.slug ?? d.permission}
            overrides={overrides}
            reasons={reasons}
            onEffect={setEffect}
            onReason={(p, v) => setReasons((r) => ({ ...r, [p]: v }))}
          />
        </Section>

        {/* Agents */}
        <Section
          icon={<Icon name="Bot" size={14} />}
          title="Agents"
          subtitle="Which agents this person can run. Denying the role's blanket access and allowing individual agents is the usual pattern."
        >
          {data.agents.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No agents registered yet.
            </p>
          ) : (
            <>
              <div className="mb-2 flex items-center gap-2 rounded-lg border border-border bg-secondary/30 px-3 py-2 text-[11px] text-muted-foreground">
                <Icon name="Info" size={12} className="shrink-0" />
                <span>
                  To restrict someone to specific agents, set{" "}
                  <code className="font-mono">agents:run:*</code> below to{" "}
                  <strong>Deny</strong>, then allow the individual agents.
                </span>
              </div>
              <DecisionRow
                permission="agents:run:*"
                label="All agents"
                decision={{
                  permission: "agents:run:*",
                  allowed: data.granted.includes("agents:run:*"),
                  source: data.denied.includes("agents:run:*")
                    ? "deny-override"
                    : data.granted.includes("agents:run:*")
                      ? "role"
                      : "default-deny",
                  pattern: "agents:run:*",
                  via_role: data.roles[0] ?? "",
                }}
                overrides={overrides}
                reasons={reasons}
                onEffect={setEffect}
                onReason={(p, v) => setReasons((r) => ({ ...r, [p]: v }))}
              />
              <DecisionTable
                decisions={data.agents}
                labelOf={(d) => d.name ?? d.permission}
                overrides={overrides}
                reasons={reasons}
                onEffect={setEffect}
                onReason={(p, v) => setReasons((r) => ({ ...r, [p]: v }))}
              />
            </>
          )}
        </Section>

        {/* Integrations */}
        <Section
          icon={<Icon name="Plug" size={14} />}
          title="Integrations"
          subtitle="Which third-party services an agent may use on this person's behalf. Separate from managing the credentials themselves, which is a capability below."
        >
          {(data.integrations ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No integrations registered yet.
            </p>
          ) : (
            <>
              <DecisionRow
                permission="integrations:use:*"
                label="All integrations"
                decision={{
                  permission: "integrations:use:*",
                  allowed: data.granted.includes("integrations:use:*"),
                  source: data.denied.includes("integrations:use:*")
                    ? "deny-override"
                    : data.granted.includes("integrations:use:*")
                      ? "role"
                      : "default-deny",
                  pattern: "integrations:use:*",
                  via_role: data.roles[0] ?? "",
                }}
                overrides={overrides}
                reasons={reasons}
                onEffect={setEffect}
                onReason={(p, v) => setReasons((r) => ({ ...r, [p]: v }))}
              />
              <DecisionTable
                decisions={data.integrations}
                labelOf={(d) => d.name ?? d.permission}
                overrides={overrides}
                reasons={reasons}
                onEffect={setEffect}
                onReason={(p, v) => setReasons((r) => ({ ...r, [p]: v }))}
              />
            </>
          )}
        </Section>

        {/* Capabilities */}
        <Section
          icon={<Icon name="Sliders" size={14} />}
          title="Administration and capabilities"
          subtitle="Platform-level permissions, separate from which apps are visible."
        >
          <DecisionTable
            decisions={data.capabilities}
            labelOf={(d) => d.permission}
            mono
            overrides={overrides}
            reasons={reasons}
            onEffect={setEffect}
            onReason={(p, v) => setReasons((r) => ({ ...r, [p]: v }))}
          />
        </Section>
      </div>
    </div>
  );
}

// ── Presentational pieces ───────────────────────────────────────────────────

function Section({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <div className="mb-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          {icon}
          {title}
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

function DecisionTable({
  decisions,
  labelOf,
  mono = false,
  overrides,
  reasons,
  onEffect,
  onReason,
}: {
  decisions: Decision[];
  labelOf: (d: Decision) => string;
  mono?: boolean;
  overrides: Overrides;
  reasons: Record<string, string>;
  onEffect: (permission: string, effect: Effect) => void;
  onReason: (permission: string, value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {decisions.map((d) => (
        <DecisionRow
          key={d.permission}
          permission={d.permission}
          label={labelOf(d)}
          mono={mono}
          decision={d}
          overrides={overrides}
          reasons={reasons}
          onEffect={onEffect}
          onReason={onReason}
        />
      ))}
    </div>
  );
}

function DecisionRow({
  permission,
  label,
  decision,
  mono = false,
  overrides,
  reasons,
  onEffect,
  onReason,
}: {
  permission: string;
  label: string;
  decision: Decision;
  mono?: boolean;
  overrides: Overrides;
  reasons: Record<string, string>;
  onEffect: (permission: string, effect: Effect) => void;
  onReason: (permission: string, value: string) => void;
}) {
  const effect: Effect = overrides[permission] ?? "inherit";
  // The server's decision reflects the SAVED state; while an override is
  // pending it would contradict the control the admin just clicked, so the
  // pending choice wins for display.
  const allowed =
    effect === "allow" ? true : effect === "deny" ? false : decision.allowed;

  return (
    <div className="rounded-xl border border-border bg-card px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <div
            className={`truncate text-sm text-foreground ${mono ? "font-mono text-[12px]" : ""}`}
          >
            {label}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {effect === "inherit"
              ? explainSource(decision)
              : effect === "allow"
                ? "allowed for this person specifically"
                : "denied for this person specifically"}
          </div>
        </div>

        <span
          className={`flex items-center gap-1.5 text-[11px] ${
            allowed ? "text-success" : "text-muted-foreground"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${allowed ? "bg-success" : "bg-muted"}`}
          />
          {allowed ? "on" : "off"}
        </span>

        <div className="flex overflow-hidden rounded-lg border border-border">
          {(["inherit", "allow", "deny"] as Effect[]).map((e) => (
            <button
              key={e}
              onClick={() => onEffect(permission, e)}
              className={`px-2.5 py-1.5 text-[11px] capitalize tech-transition ${
                effect === e
                  ? e === "deny"
                    ? "bg-destructive/15 text-destructive"
                    : e === "allow"
                      ? "bg-primary/15 text-primary"
                      : "bg-secondary text-foreground"
                  : "text-muted-foreground hover:bg-secondary/60"
              }`}
            >
              {e}
            </button>
          ))}
        </div>
      </div>

      {effect !== "inherit" && (
        <input
          value={reasons[permission] ?? ""}
          onChange={(ev) => onReason(permission, ev.target.value)}
          placeholder="Why? (recorded alongside the exception)"
          className="mt-2 w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-[11px] text-foreground outline-none focus:border-primary/50"
        />
      )}
    </div>
  );
}
