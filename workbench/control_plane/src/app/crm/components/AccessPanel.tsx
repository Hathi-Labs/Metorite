"use client";

/**
 * Who may open the CRM (WS-53 CRM-L2, spec §10 item 6, CRM-T5).
 *
 * An admin chooses here: admins only, everyone, or chosen groups. The write is
 * `PATCH /crm/settings {access, access_groups}`, floored on
 * `admin:access:manage` server-side. Until an admin chooses, the rule reads as
 * "admins only", and this panel says so and links to its own control.
 *
 * `AccessPanel` only draws. `AccessSection` reads, writes and owns the state.
 */

import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import SelectButton from "@/components/ui/SelectButton";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  ACCESS_ANCHOR,
  ACCESS_OPTIONS,
  ADMINS_ONLY_HINT,
  adminsOnly,
  groupSubject,
  toggleGroup,
  wantsGroups,
  type CrmAccess,
} from "../lib/access";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import type { CrmSettings } from "../lib/connect";
import { CRM_OFF, invalidateCrmMode, readSettings } from "../lib/useCrmMode";

export type GroupRow = { slug: string; display_name?: string };

export default function AccessPanel({
  access,
  groups,
  allGroups,
  saving,
  onChange,
}: {
  access: string | undefined | null;
  /** The `group:<slug>` subjects that the rule names. */
  groups: readonly string[];
  allGroups: readonly GroupRow[];
  saving: boolean;
  onChange: (access: CrmAccess, groups: string[]) => void;
}) {
  const value: CrmAccess =
    access === "everyone" || access === "groups" ? access : "admins";
  return (
    <section
      id={ACCESS_ANCHOR}
      className="rounded-xl border border-border bg-card/40"
      aria-label="CRM access"
    >
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Access</h2>
        {adminsOnly(access) ? (
          <p className="mt-0.5 text-xs text-muted-foreground">
            {ADMINS_ONLY_HINT}
          </p>
        ) : (
          <p className="mt-0.5 text-xs text-muted-foreground">
            Admins can always open the CRM. Choose who else can.
          </p>
        )}
      </header>
      <div className="flex flex-col gap-3 px-4 py-3">
        <SelectButton
          label="Who can open the CRM"
          value={value}
          options={ACCESS_OPTIONS.map((o) => ({
            value: o.value,
            label: o.label,
            description: o.description,
          }))}
          defaultValue="admins"
          disabled={saving}
          onChange={(next) =>
            onChange(next as CrmAccess, next === "groups" ? [...groups] : [])
          }
        />
        {value === "groups" && (
          <fieldset className="flex flex-col gap-1.5" disabled={saving}>
            <legend className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              Groups
            </legend>
            {allGroups.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No groups yet. Add a group in Teams, then pick it here.
              </p>
            ) : (
              allGroups.map((g) => {
                const subject = groupSubject(g.slug);
                return (
                  <label
                    key={g.slug}
                    className="flex items-center gap-2 text-xs text-foreground"
                  >
                    <Checkbox
                      size="sm"
                      checked={groups.includes(subject)}
                      onChange={() =>
                        onChange("groups", toggleGroup(groups, subject))
                      }
                    />
                    {g.display_name || g.slug}
                  </label>
                );
              })
            )}
          </fieldset>
        )}
      </div>
    </section>
  );
}

/** The panel with its reads and its write. Admins only: the page gates it. */
export function AccessSection() {
  const toast = useToast();
  // The same cache entry as the mode read (`useCrmMode`), the same fetcher.
  const settings = useCachedResource<CrmSettings | null | typeof CRM_OFF>(
    "crm:settings",
    readSettings,
  );
  const [groups, setGroups] = useState<GroupRow[]>([]);
  const [groupsError, setGroupsError] = useState(false);
  const [groupsTry, setGroupsTry] = useState(0);
  const [saving, setSaving] = useState(false);
  const access =
    typeof settings.data === "object" ? settings.data?.access : undefined;
  // The group list is read only when the rule is "groups", and only after the
  // settings have loaded (spec section 18.6). It is the one request this panel
  // makes that the page could do without.
  const needGroups = !settings.loading && wantsGroups(access);

  useEffect(() => {
    if (!needGroups) return;
    let live = true;
    setGroupsError(false);
    const read = api.listGroups();
    read.then(
      (rows) => {
        if (live) setGroups(rows);
      },
      () => {
        if (live) setGroupsError(true);
      },
    );
    return () => {
      live = false;
    };
  }, [needGroups, groupsTry]);

  if (settings.loading) {
    return (
      <div
        className="space-y-2 rounded-xl border border-border bg-card/40 px-4 py-3"
        aria-label="Loading access"
      >
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-56" />
      </div>
    );
  }
  if (settings.error && settings.data === undefined) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card/40 px-4 py-3">
        <p className="text-xs text-destructive">
          Could not load the access rule. Try again.
        </p>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => settings.refresh()}
        >
          Try again
        </Button>
      </div>
    );
  }

  async function change(access: CrmAccess, accessGroups: string[]) {
    setSaving(true);
    try {
      await api.saveAccess({ access, access_groups: accessGroups });
      invalidateCrmMode();
      toast.show({
        key: "crm-access",
        variant: "success",
        title: "Access saved",
      });
    } catch (err) {
      toast.show({
        key: "crm-access",
        variant: "error",
        title:
          err instanceof CrmError
            ? err.message
            : "The access did not save. Try again.",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {groupsError && (
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <p className="text-xs text-destructive">
            Could not load the groups. Try again.
          </p>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setGroupsTry((n) => n + 1)}
          >
            Try again
          </Button>
        </div>
      )}
      <AccessPanel
        access={access}
        groups={
          (typeof settings.data === "object" && settings.data?.access_groups) ||
          []
        }
        allGroups={groups}
        saving={saving}
        onChange={change}
      />
    </>
  );
}
