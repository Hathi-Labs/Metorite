"use client";

// WhatsApp Categories — the settings surface where labels become policy.
// A settings screen, so density is allowed (you navigated here on purpose):
// each category answers notify / auto-reply / drafts / escalation. Only the
// meaningful cell should draw the eye; defaults stay quiet.

import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import {
  bootstrapCategories,
  fetchAccounts,
  pickDefaultAccount,
  fetchCategories,
  updateCategory,
} from "../../lib/api";
import {
  AUTO_REPLY_POLICIES,
  DRAFT_POLICIES,
  NOTIFY_POLICIES,
  type WaCategory,
} from "../../lib/types";

export default function CategoriesSettingsPage() {
  const [loading, setLoading] = useState(true);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [rows, setRows] = useState<WaCategory[]>([]);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const id = pickDefaultAccount(await fetchAccounts())?.id ?? null;
      setAccountId(id);
      if (id) setRows(await fetchCategories(id));
      setLoading(false);
    })();
  }, []);

  const onBootstrap = useCallback(async () => {
    if (!accountId) return;
    setSaving("bootstrap");
    const res = await bootstrapCategories(accountId);
    if (res.ok && res.data) setRows(res.data);
    setSaving(null);
  }, [accountId]);

  const onPatch = useCallback(
    async (cat: WaCategory, field: keyof WaCategory, value: string) => {
      setSaving(cat.id);
      setError(null);
      const prevValue = cat[field];
      // optimistic update
      setRows((prev) =>
        prev.map((r) => (r.id === cat.id ? { ...r, [field]: value } : r))
      );
      const res = await updateCategory(cat.id, { [field]: value });
      if (!res.ok) {
        // roll the optimistic change back and surface the failure
        setRows((prev) =>
          prev.map((r) => (r.id === cat.id ? { ...r, [field]: prevValue } : r))
        );
        setError(res.error ?? "Couldn't save that change.");
      }
      setSaving(null);
    },
    []
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Icon name="Loader2" className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  return (
    <div className="mx-auto h-full max-w-3xl overflow-y-auto p-4 text-foreground md:p-6">
      <div className="mb-5 flex items-center gap-2">
        <Icon name="Tags" className="h-4 w-4 text-primary" />
        <h2 className="text-[15px] font-semibold">Categories</h2>
        <span className="text-[11px] text-muted-foreground">
          labels, upgraded to policy
        </span>
      </div>

      {error && (
        <div className="mb-3 rounded-md bg-red-500/10 px-3 py-1.5 text-[11px] text-red-500">
          {error}
        </div>
      )}

      {!accountId ? (
        <EmptyNoAccount />
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-border p-6 text-center">
          <p className="text-[13px] text-muted-foreground">
            No categories yet. Seed the default policy set to get started.
          </p>
          <button
            onClick={onBootstrap}
            disabled={saving === "bootstrap"}
            className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground disabled:opacity-50"
          >
            {saving === "bootstrap" && (
              <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
            )}
            Seed default categories
          </button>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[520px] text-[12px]">
            <thead>
              <tr className="border-b border-border text-[9.5px] uppercase tracking-wider text-muted-foreground/70">
                <th className="px-3 py-2 text-left font-bold">Category</th>
                <th className="px-3 py-2 text-left font-bold">Notify</th>
                <th className="px-3 py-2 text-left font-bold">Auto-reply</th>
                <th className="px-3 py-2 text-left font-bold">AI drafts</th>
                <th className="px-3 py-2 text-left font-bold">Escalate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-b border-border last:border-0">
                  <td className="px-3 py-2.5">
                    <span className="font-semibold">{c.name}</span>
                    {c.wa_label_id && (
                      <span className="ml-1 text-[10px] text-success">🏷</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <PolicySelect
                      value={c.notify_policy}
                      options={NOTIFY_POLICIES}
                      busy={saving === c.id}
                      onChange={(v) => onPatch(c, "notify_policy", v)}
                    />
                  </td>
                  <td className="px-3 py-2.5">
                    <PolicySelect
                      value={c.auto_reply_policy}
                      options={AUTO_REPLY_POLICIES}
                      busy={saving === c.id}
                      onChange={(v) => onPatch(c, "auto_reply_policy", v)}
                    />
                  </td>
                  <td className="px-3 py-2.5">
                    <PolicySelect
                      value={c.draft_policy}
                      options={DRAFT_POLICIES}
                      busy={saving === c.id}
                      onChange={(v) => onPatch(c, "draft_policy", v)}
                    />
                  </td>
                  <td className="px-3 py-2.5 text-muted-foreground">
                    {c.escalate_after_mins
                      ? `${Math.round(c.escalate_after_mins / 60)}h`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-4 text-[11px] text-muted-foreground/70">
        Guardrails still apply above any policy: VIP and Family never auto-send —
        the most the AI does there is prepare a draft you send.
      </p>
    </div>
  );
}

function PolicySelect({
  value,
  options,
  busy,
  onChange,
}: {
  value: string;
  options: string[];
  busy: boolean;
  onChange: (v: string) => void;
}) {
  // "never" is the quiet default; a non-never policy is the meaningful signal.
  const muted = value === "never";
  return (
    <select
      value={value}
      disabled={busy}
      onChange={(e) => onChange(e.target.value)}
      className={`rounded-md border border-border bg-background px-2 py-1 text-[11px] outline-none disabled:opacity-50 ${
        muted ? "text-muted-foreground" : "text-foreground"
      }`}
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o.replace(/_/g, " ")}
        </option>
      ))}
    </select>
  );
}

function EmptyNoAccount() {
  return (
    <div className="rounded-lg border border-border p-6 text-center text-[13px] text-muted-foreground">
      Connect a WhatsApp Business number first, then set category policies here.
    </div>
  );
}
