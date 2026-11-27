"use client";

/**
 * "Who can use" one connection (ACC-2, `integrations_console.md` §3.9.5).
 *
 * `WhoCanUseRow` is the control under a row of Connections. It opens
 * `WhoCanUseDialog`, where an admin picks Everyone, chosen teams and people,
 * or Admins only. `UnmatchedRules` shows each stored rule that no row shows,
 * so an admin can still change or clear it. `AccessNotice` says that the rules
 * did not load, or that the stored value cannot be read.
 *
 * The rules of what draws and what the words say are in `lib/whoCanUse.ts`.
 * For an org off `INTEGRATION_ACCESS_ORGS` the read is `null`, and nothing
 * here draws.
 */

import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useViewMode } from "@/components/ViewModeProvider";
import { useCachedResource } from "@/lib/useCachedResource";

import { saveAccessRule } from "../lib/accessSave";
import * as api from "../lib/api";
import {
  EMPTY_CHOICE_WARNING,
  LOAD_FAILED,
  MODE_CHOICES,
  RULE_FACTS,
  RULE_GONE,
  SAVE_FAILED,
  UNREADABLE_ALL,
  changedRule,
  changedText,
  draftOf,
  editDraft,
  emptyChoice,
  filterChoices,
  followRule,
  groupChoices,
  personChoices,
  ruleBody,
  ruleSummary,
  sameRule,
  saveLabel,
  toggleValue,
  type AccessItem,
  type AccessList,
  type AccessMode,
  type PickOption,
} from "../lib/whoCanUse";

const GROUPS_KEY = "integrations:access-groups";
const PEOPLE_KEY = "integrations:access-people";

// ── The row control ───────────────────────────────────────────────────────

export function WhoCanUseRow({
  item,
  listReadable,
  label,
  onOpen,
}: {
  item: AccessItem;
  /** False while the whole stored value cannot be read: no save can work. */
  listReadable: boolean;
  /** The row's name, for a screen reader. */
  label: string;
  onOpen: () => void;
}) {
  const answer = ruleSummary(item, listReadable);
  return (
    // `pl-11` lines the control up with the name of the row above it.
    <div className="-mt-1 pb-2 pl-11 pr-4" data-who-can-use={item.service}>
      {listReadable ? (
        <Button
          variant="secondary"
          size="sm"
          icon="Users"
          aria-haspopup="dialog"
          aria-label={`Who can use: ${answer} (${label})`}
          onClick={onOpen}
          className="max-w-full"
        >
          <span className="truncate">Who can use: {answer}</span>
        </Button>
      ) : (
        // No save can work, so this is a fact and not a dead control.
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Icon name="Users" className="h-3.5 w-3.5 shrink-0" />
          Who can use: {answer}
        </p>
      )}
    </div>
  );
}

// ── The notice above the list ─────────────────────────────────────────────

export function AccessNotice({
  list,
  error,
  onRetry,
}: {
  list: AccessList | null | undefined;
  error: string | null;
  onRetry: () => void;
}) {
  if (list === undefined && error) {
    return (
      <div
        role="alert"
        className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive"
      >
        <span className="min-w-0 break-words">
          {error === LOAD_FAILED ? LOAD_FAILED : `${LOAD_FAILED} ${error}`}
        </span>
        <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }
  if (list && !list.readable) {
    return (
      <p
        role="alert"
        className="flex shrink-0 items-start gap-2 border-b border-warning/30 bg-warning/10 px-4 py-2 text-xs text-foreground"
      >
        <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
        <span className="min-w-0">{UNREADABLE_ALL}</span>
      </p>
    );
  }
  return null;
}

// ── Stored rules that no row shows ────────────────────────────────────────

export function UnmatchedRules({
  items,
  onChange,
  onClear,
}: {
  items: readonly AccessItem[];
  onChange: (item: AccessItem) => void;
  onClear: (item: AccessItem) => void;
}) {
  if (items.length === 0) return null;
  return (
    <section aria-label="Other stored rules" className="border-t border-border px-4 py-3">
      <h3 className="text-xs font-semibold text-foreground">Other stored rules</h3>
      <p className="mt-0.5 text-xs text-muted-foreground">
        No control above covers these integrations. Each rule stays until you change it or set it
        to Everyone.
      </p>
      <ul className="mt-1 divide-y divide-border">
        {items.map((item) => (
          <li key={item.service} className="flex flex-wrap items-center gap-2 py-2">
            <span className="min-w-0 flex-1">
              <span className="block break-all text-sm font-medium text-foreground">{item.service}</span>
              <span className="block text-xs text-muted-foreground">Who can use: {ruleSummary(item)}</span>
            </span>
            {item.listed ? (
              <Button
                variant="secondary"
                size="sm"
                icon="Users"
                aria-haspopup="dialog"
                aria-label={`Change who can use ${item.service}`}
                onClick={() => onChange(item)}
              >
                Change
              </Button>
            ) : (
              <Button
                variant="secondary"
                size="sm"
                icon="RotateCcw"
                aria-haspopup="dialog"
                aria-label={`Set to Everyone: ${item.service}`}
                onClick={() => onClear(item)}
              >
                Set to Everyone
              </Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ── The editor ────────────────────────────────────────────────────────────

/** The state of one picker list. */
export interface PickList {
  options: PickOption[];
  loading: boolean;
  /** The read failed, and there is nothing to show from before. */
  error: string | null;
  onRetry: () => void;
}

function Picker({
  title,
  list,
  picked,
  query,
  onQuery,
  onToggle,
  empty,
  disabled,
}: {
  title: string;
  list: PickList;
  picked: readonly string[];
  /** Present on a list with a search box. */
  query?: string;
  onQuery?: (value: string) => void;
  onToggle: (value: string) => void;
  /** What an empty list says. */
  empty: string;
  disabled: boolean;
}) {
  const shown = filterChoices(list.options, query ?? "");
  const noun = title.toLowerCase();
  const failed = `Could not read the ${noun}.`;
  return (
    <fieldset className="flex min-w-0 flex-col gap-1" disabled={disabled}>
      <legend className="mb-1 text-xs font-semibold text-foreground">
        {title}
        {picked.length ? (
          <span className="ml-1.5 font-normal text-muted-foreground">{picked.length} picked</span>
        ) : null}
      </legend>
      {onQuery && list.options.length > 0 ? (
        <Input
          type="search"
          icon="Search"
          value={query ?? ""}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search by name or email"
          aria-label={`Search ${noun}`}
        />
      ) : null}
      {list.loading ? (
        <div role="status" aria-label={`Loading ${noun}`}>
          <SkeletonRows count={3} />
        </div>
      ) : null}
      {list.error ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive">
          <span className="min-w-0 break-words">
            {list.error === failed ? failed : `${failed} ${list.error}`}
          </span>
          <Button variant="secondary" size="sm" icon="RefreshCw" onClick={list.onRetry}>
            Try again
          </Button>
        </div>
      ) : null}
      {!list.loading && !list.error && list.options.length === 0 ? (
        <p className="text-xs text-muted-foreground">{empty}</p>
      ) : null}
      {list.options.length > 0 && shown.length === 0 ? (
        <p role="status" className="text-xs text-muted-foreground">
          No match for &ldquo;{query?.trim()}&rdquo;.
        </p>
      ) : null}
      {shown.length > 0 ? (
        <ul className="flex flex-col">
          {shown.map((o) => (
            <li key={o.value}>
              <label className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 hover:bg-secondary">
                <Checkbox
                  size="sm"
                  checked={picked.includes(o.value)}
                  onChange={() => onToggle(o.value)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-foreground">{o.label}</span>
                  {o.hint ? (
                    <span className="block truncate text-[11px] text-muted-foreground">{o.hint}</span>
                  ) : null}
                </span>
              </label>
            </li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

export interface WhoCanUseBodyProps {
  /** The stored rule that the choices started from. */
  item: AccessItem;
  /** A newer stored rule. Save stays off until the admin reloads it. */
  changed: AccessItem | null;
  onReload: () => void;
  mode: AccessMode;
  onMode: (mode: AccessMode) => void;
  groups: readonly string[];
  people: readonly string[];
  onGroup: (slug: string) => void;
  onPerson: (email: string) => void;
  teams: PickList;
  members: PickList;
  query: string;
  onQuery: (value: string) => void;
  saving: boolean;
  /** The refusal of the last save, in the gateway's words. */
  error: string | null;
  onSave: () => void;
  onCancel: () => void;
}

export function WhoCanUseBody({
  item,
  changed,
  onReload,
  mode,
  onMode,
  groups,
  people,
  onGroup,
  onPerson,
  teams,
  members,
  query,
  onQuery,
  saving,
  error,
  onSave,
  onCancel,
}: WhoCanUseBodyProps) {
  const unchanged = sameRule(item, ruleBody(mode, groups, people));
  const empty = emptyChoice(mode, groups, people);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3">
        <p className="text-xs text-muted-foreground">{RULE_FACTS}</p>
        {!item.readable ? (
          <p className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-foreground">
            <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
            <span>
              Metorite cannot read the stored rule, so only admins can use this integration. A save
              replaces it.
            </span>
          </p>
        ) : null}
        {/* The radio group of `Button`s, as the logo editor draws it. */}
        <div role="radiogroup" aria-label="Who can use it" className="flex shrink-0 flex-col gap-1.5">
          {MODE_CHOICES.map((c) => (
            <Button
              key={c.value}
              variant="secondary"
              size="none"
              layout="flex flex-col items-start"
              role="radio"
              aria-checked={mode === c.value}
              selected={mode === c.value}
              disabled={saving}
              onClick={() => onMode(c.value)}
              className="gap-0.5 px-3 py-2 text-left"
            >
              <span className="text-sm font-medium text-foreground">{c.label}</span>
              <span className="text-xs text-muted-foreground">{c.description}</span>
            </Button>
          ))}
        </div>
        {mode === "chosen" ? (
          <>
            <Picker
              title="Teams"
              list={teams}
              picked={groups}
              onToggle={onGroup}
              empty="No teams yet. Add a team in Settings, then pick it here."
              disabled={saving}
            />
            <Picker
              title="People"
              list={members}
              picked={people}
              query={query}
              onQuery={onQuery}
              onToggle={onPerson}
              empty="No members yet."
              disabled={saving}
            />
          </>
        ) : null}
      </div>
      {/* Outside the scroll box, so each line shows at the moment of a save. */}
      {changed ? (
        <div
          role="alert"
          className="flex shrink-0 flex-wrap items-start justify-end gap-2 border-t border-warning/30 bg-warning/10 px-3 py-2 text-xs text-foreground"
        >
          <p className="flex w-full items-start gap-2">
            <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
            <span className="min-w-0">{changedText(changed)}</span>
          </p>
          <Button
            variant="secondary"
            size="sm"
            icon="RefreshCw"
            disabled={saving}
            onClick={onReload}
            data-notice=""
          >
            Reload the rule
          </Button>
        </div>
      ) : null}
      {empty && !changed ? (
        <p
          role="status"
          className="flex shrink-0 items-start gap-2 border-t border-warning/30 bg-warning/10 px-3 py-2 text-xs text-foreground"
        >
          <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
          <span>{EMPTY_CHOICE_WARNING}</span>
        </p>
      ) : null}
      {error ? (
        <p
          role="alert"
          tabIndex={-1}
          data-notice=""
          className="shrink-0 break-words border-t border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          {error}
        </p>
      ) : null}
      <div className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-3 py-2">
        <Button variant="secondary" size="sm" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          loading={saving}
          disabled={unchanged || changed !== null}
          onClick={onSave}
        >
          {saveLabel(mode, groups, people)}
        </Button>
      </div>
    </div>
  );
}

/**
 * The dialog of one rule. The caller keys it by the service, so each service
 * starts from its own stored rule. A refused save keeps every choice.
 *
 * `item` is live. A dialog with no choice yet follows a newer rule. After a
 * choice, a newer rule stops Save until the admin reloads it.
 */
export default function WhoCanUseDialog({
  item,
  label,
  onClose,
  onSaved,
}: {
  item: AccessItem;
  label: string;
  onClose: () => void;
  onSaved: (saved: AccessItem) => void;
}) {
  const { isMobile } = useViewMode();
  const teamsRead = useCachedResource(GROUPS_KEY, api.getAccessGroups);
  const peopleRead = useCachedResource(PEOPLE_KEY, api.getAccessPeople);
  const [draft, setDraft] = useState(() => draftOf(item));
  // The newest rule that the dialog heard: the live `item`, or the read that
  // runs before a save.
  const [heard, setHeard] = useState({ item, now: item });
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const stopped = useRef(false);
  const { mode, groups, people } = draft;

  // Save goes off while it runs, so a stopped save leaves the focus on the
  // page body. Move it to "Reload the rule", or to the words that say why.
  useEffect(() => {
    if (saving || !stopped.current) return;
    stopped.current = false;
    bodyRef.current?.querySelector<HTMLElement>("[data-notice]")?.focus();
  }, [saving]);

  // Adjusted during render, so no frame draws the old rule as the stored one.
  if (heard.item !== item) setHeard({ item, now: item });
  const followed = saving ? draft : followRule(draft, heard.now);
  if (followed !== draft) {
    setDraft(followed);
    setError(null);
  }
  // A save in flight can bring its own change, so it raises no notice.
  const changed = saving ? null : changedRule(draft, heard.now);

  // A stored name that left the list stays on screen after an untick.
  const keep = (stored: string[], now: string[]) => Array.from(new Set([...stored, ...now]));
  const teams: PickList = {
    options: groupChoices(teamsRead.data ?? [], keep(draft.base.groups, groups), teamsRead.data !== undefined),
    loading: teamsRead.loading,
    error: teamsRead.data === undefined ? teamsRead.error : null,
    onRetry: teamsRead.refresh,
  };
  const members: PickList = {
    options: personChoices(peopleRead.data ?? [], keep(draft.base.people, people), peopleRead.data !== undefined),
    loading: peopleRead.loading,
    error: peopleRead.data === undefined ? peopleRead.error : null,
    onRetry: peopleRead.refresh,
  };

  /** A change of a choice makes the last refusal old. */
  function choose(change: Parameters<typeof editDraft>[1]) {
    setError(null);
    setDraft(editDraft(draft, change));
  }

  function reload() {
    // The button leaves the screen with the notice, so the focus moves first,
    // to the radio of the stored mode.
    const at = MODE_CHOICES.findIndex((c) => c.value === heard.now.mode);
    bodyRef.current?.querySelectorAll<HTMLElement>('[role="radio"]')[at]?.focus();
    setError(null);
    setDraft(draftOf(heard.now));
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const outcome = await saveAccessRule(draft.base, ruleBody(mode, groups, people));
      if (outcome.kind === "saved") {
        onSaved(outcome.item);
        return;
      }
      stopped.current = true;
      if (outcome.kind === "changed") {
        setHeard((h) => ({ ...h, now: outcome.current }));
        setDraft((d) => editDraft(d, {}));
      } else {
        setError(RULE_GONE);
      }
    } catch (err) {
      // A network fault has the browser's words, so it gets ours.
      setError(err instanceof api.IntegrationsError ? err.message : SAVE_FAILED);
      // A 422 can name a team or a person that left since the lists loaded.
      if (err instanceof api.IntegrationsError && err.status === 422) {
        teamsRead.refresh();
        peopleRead.refresh();
      }
    }
    setSaving(false);
  }

  return (
    <Modal
      open
      onClose={() => {
        if (!saving) onClose();
      }}
      title={`Who can use ${label}`}
      icon="Users"
      size="md"
      // The top stays put when the pickers open, so no choice moves under the
      // pointer. A phone sheet takes the full height for the same reason.
      placement={isMobile ? "sheet" : "top"}
      className={isMobile ? "h-full" : ""}
      initialFocus={() =>
        bodyRef.current?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]') ?? true
      }
    >
      <div ref={bodyRef} className="flex min-h-0 flex-1 flex-col">
        <WhoCanUseBody
          item={draft.base}
          changed={changed}
          onReload={reload}
          mode={mode}
          onMode={(next) => choose({ mode: next })}
          groups={groups}
          people={people}
          onGroup={(slug) => choose({ groups: toggleValue(groups, slug) })}
          onPerson={(email) => choose({ people: toggleValue(people, email) })}
          teams={teams}
          members={members}
          query={query}
          onQuery={setQuery}
          saving={saving}
          error={error}
          onSave={save}
          onCancel={onClose}
        />
      </div>
    </Modal>
  );
}
