"use client";

/**
 * Settings, Home layout: the form (`package_host.md` §9.1, PKG-17c-2).
 *
 * The interaction: an org admin opens the page and sees five roles. For each
 * role the admin ticks the apps to star, picks a preset, or presses "Use the
 * system default". Save turns on when the draft differs from the read. After
 * a good save the text "Saved" shows, and the shell shows the new default.
 *
 * `HomeLayoutView` is pure: it draws from props. `HomeLayoutForm` owns the
 * read, the draft and the save. A fault never allows a Save. An empty read
 * asks once before the first Save.
 */
import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { SelectButton, type SelectOption } from "@/components/ui/SelectButton";
import { Skeleton } from "@/components/ui/Skeleton";
import { EMPTY_ORG_LAYOUT, ROLE_ORDER, type OrgLayout, type OrgRole } from "@/lib/shell/orgLayout";
import { readOrgLayoutRaw, type OrgLayoutRead } from "@/lib/shell/orgLayoutAdmin";
import { PRESETS } from "@/lib/shell/presets";

import {
  CONFIRM_BODY,
  FAULT_TEXT,
  HELPER,
  OFF_TEXT,
  SYSTEM_DEFAULT,
  commitDraft,
  needsConfirm,
  pickPreset,
  pinsOf,
  presetOf,
  sameLayout,
  starrablePanes,
  togglePin,
  clearRole,
} from "./homeLayout";

const ROLE_LABEL: Record<OrgRole, string> = {
  owner: "Owner",
  admin: "Admin",
  manager: "Manager",
  member: "Member",
  guest: "Guest",
};

const PRESET_OPTIONS: SelectOption[] = [
  { value: "", label: SYSTEM_DEFAULT },
  ...PRESETS.map((p) => ({ value: p.id, label: p.label })),
];

export type ReadState = { kind: "loading" } | OrgLayoutRead;

export interface ViewProps {
  read: ReadState;
  draft: OrgLayout;
  dirty: boolean;
  busy: boolean;
  error: string;
  refusal: string;
  saved: boolean;
  confirmOpen: boolean;
  onToggle: (role: OrgRole, href: string, on: boolean) => void;
  onPreset: (role: OrgRole, id: string) => void;
  onReset: (role: OrgRole) => void;
  onSave: () => void;
  onRetry: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function HomeLayoutView(p: ViewProps) {
  if (p.read.kind === "loading") {
    return (
      <div aria-busy="true">
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }
  if (p.read.kind === "off") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-secondary/30 px-4 py-5 text-sm text-foreground">
        <Icon name="PowerOff" size={15} className="text-muted-foreground" />
        {OFF_TEXT}
      </div>
    );
  }
  if (p.read.kind === "fault") {
    return (
      <div
        role="alert"
        className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
      >
        <span className="flex-1">{FAULT_TEXT}</span>
        <Button size="sm" variant="secondary" onClick={p.onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  const panes = starrablePanes();
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-muted-foreground">{HELPER}</p>
      {ROLE_ORDER.map((role) => {
        const pins = pinsOf(p.draft, role) ?? [];
        return (
          <section key={role} className="flex flex-col gap-3 rounded-xl border border-border p-4 sm:p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-foreground">{ROLE_LABEL[role]}</h2>
              <span className="ml-auto flex flex-wrap items-center justify-end gap-2">
                <SelectButton
                  label={`Preset for ${ROLE_LABEL[role]}`}
                  value={presetOf(p.draft, role) ?? ""}
                  defaultValue=""
                  options={PRESET_OPTIONS}
                  onChange={(id) => p.onPreset(role, id)}
                  widthClass="w-48"
                />
                <Button size="sm" variant="ghost" onClick={() => p.onReset(role)}>
                  Use the system default
                </Button>
              </span>
            </div>
            <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {panes.map((pane) => (
                <li key={pane.href}>
                  <label className="flex items-center gap-2 text-xs text-foreground">
                    <Checkbox
                      checked={pins.includes(pane.href)}
                      onChange={(e) => p.onToggle(role, pane.href, e.target.checked)}
                      aria-label={`${pane.label} for ${ROLE_LABEL[role]}`}
                    />
                    <Icon name={pane.icon} size={14} className="text-muted-foreground" />
                    {pane.label}
                  </label>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      {p.refusal ? (
        <p role="alert" className="text-xs text-destructive">
          {p.refusal}
        </p>
      ) : null}
      {p.error ? (
        <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
          {p.error}
        </p>
      ) : null}
      <div className="flex items-center gap-3">
        <Button size="sm" icon="Check" onClick={p.onSave} loading={p.busy} disabled={!p.dirty || p.busy}>
          {p.busy ? "Saving…" : "Save"}
        </Button>
        {p.saved ? (
          <span role="status" className="text-xs text-success">
            Saved
          </span>
        ) : null}
      </div>
      <ConfirmDialog
        open={p.confirmOpen}
        title="Replace the default?"
        body={CONFIRM_BODY}
        confirmLabel="Save"
        cancelLabel="Cancel"
        confirmVariant="primary"
        defaultFocus="cancel"
        onConfirm={p.onConfirm}
        onCancel={p.onCancel}
      />
    </div>
  );
}

export function HomeLayoutForm() {
  const [read, setRead] = useState<ReadState>({ kind: "loading" });
  const [loaded, setLoaded] = useState<OrgLayout>(EMPTY_ORG_LAYOUT);
  const [draft, setDraft] = useState<OrgLayout>(EMPTY_ORG_LAYOUT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refusal, setRefusal] = useState("");
  const [saved, setSaved] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    void readOrgLayoutRaw().then((r) => {
      if (!live) return;
      if (r.kind === "ok") {
        setLoaded(r.layout);
        setDraft(r.layout);
      }
      setRead(r);
    });
    return () => {
      live = false;
    };
  }, [attempt]);

  const touch = (next: OrgLayout) => {
    setDraft(next);
    setSaved(false);
    setError("");
  };

  const doSave = async () => {
    setConfirmOpen(false);
    setBusy(true);
    setError("");
    setSaved(false);
    const res = await commitDraft(draft);
    setBusy(false);
    if (res.kind === "saved") {
      setLoaded(res.layout);
      setDraft(res.layout);
      setConfirmed(true);
      setSaved(true);
    } else {
      setError(res.reason);
    }
  };

  const onSave = () => {
    if (read.kind !== "ok" || busy || sameLayout(draft, loaded)) return;
    if (needsConfirm(read.empty, confirmed)) setConfirmOpen(true);
    else void doSave();
  };

  return (
    <HomeLayoutView
      read={read}
      draft={draft}
      dirty={!sameLayout(draft, loaded)}
      busy={busy}
      error={error}
      refusal={refusal}
      saved={saved}
      confirmOpen={confirmOpen}
      onToggle={(role, href, on) => {
        const c = togglePin(draft, role, href, on);
        setRefusal(c.refusal ?? "");
        if (!c.refusal) touch(c.layout);
      }}
      onPreset={(role, id) => touch(pickPreset(draft, role, id))}
      onReset={(role) => touch(clearRole(draft, role))}
      onSave={onSave}
      onRetry={() => {
        setRead({ kind: "loading" });
        setAttempt((n) => n + 1);
      }}
      onConfirm={() => void doSave()}
      onCancel={() => setConfirmOpen(false)}
    />
  );
}
