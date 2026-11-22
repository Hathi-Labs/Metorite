"use client";

/**
 * Change access — the panel of one of the member's own agents (WS-55
 * EA-17c-2, D-EA-13).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §10.8. It opens under
 * the row, with the grant that the agent holds. It reuses the project tree of
 * the consent page (`AccessTree`, `lib/accessTree.ts`) and the same words.
 * Each scope switch has one of three states (`scopeState` in
 * `../lib/changeAccess.ts`), and Save sends the whole grant in one PATCH.
 *
 * ⚠️ An empty `pinned_projects` means every project. So after "Only the ones
 * I pick" the panel never sends `[]`: Save stays off until one project is
 * ticked. `buildChange` holds that rule, and `AgentsView.test.ts` fences it.
 */
import { useEffect, useMemo, useState } from "react";

import AccessTree from "@/components/AccessTree";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { SCOPE_COPY } from "@/app/connect/authorize/consent";
import {
  TOO_LONG_NOTICE,
  buildProjectTree,
  type ProjectMode,
  type ProjectTree,
} from "@/lib/accessTree";

import type { AgentConnection } from "../lib/agents";
import {
  buildChange,
  changeSummary,
  initialDraft,
  sameAsRow,
  saveChange,
  scopeNote,
  scopeState,
  splitMarks,
  staleNotice,
  switchScopes,
  toggleDraftScope,
  type Draft,
} from "../lib/changeAccess";

const KNOWN = Object.keys(SCOPE_COPY);

export interface AgentAccessFormProps {
  row: AgentConnection;
  /** `null` while the list failed to load. */
  tree: ProjectTree | null;
  onSaved: (row: AgentConnection) => void;
  onCancel: () => void;
}

/** The form, given the project list. The wrapper below reads the list. */
export function AgentAccessForm({ row, tree, onSaved, onCancel }: AgentAccessFormProps) {
  const [draft, setDraft] = useState<Draft>(() => initialDraft(row, tree, KNOWN));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scopes = useMemo(() => switchScopes(row, KNOWN), [row]);
  const built = buildChange(row, draft, tree, KNOWN);
  const unchanged = built.ok && sameAsRow(row, built.body);
  const stale = tree ? splitMarks(row, tree).stale : 0;
  const summary = changeSummary(row, draft, tree, built.ok ? built.personalPinned : 0);
  const notice = staleNotice(draft.mode === "pick" ? stale : 0);
  const problem = built.ok ? null : built.problem;

  async function save(): Promise<void> {
    if (!built.ok || unchanged || saving) return;
    setSaving(true);
    setError(null);
    try {
      onSaved(await saveChange(row.id, built.body));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  }

  return (
    <div data-testid="change-access-panel" className="flex flex-col gap-4 border-t border-border pt-3">
      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-semibold text-foreground">What it may do</legend>
        <ul className="flex flex-col gap-3">
          {scopes.map((scope) => {
            const copy = SCOPE_COPY[scope];
            const state = scopeState(row, scope);
            const note = scopeNote(row, scope);
            return (
              <li key={scope} data-testid={`scope-${scope}`} data-state={state} className="flex items-start justify-between gap-3">
                <div className="min-w-0 text-sm">
                  <span className="block text-foreground">{copy.label}</span>
                  <span className="block text-xs text-muted-foreground">{copy.detail}</span>
                  {note && (
                    <span data-testid="scope-note" className="mt-0.5 block text-xs text-warning">
                      {note}
                    </span>
                  )}
                </div>
                <Switch
                  label={copy.label}
                  checked={state === "toggle" && draft.scopes.has(scope)}
                  disabled={state !== "toggle" || saving}
                  onChange={() => setDraft({ ...draft, scopes: toggleDraftScope(row, draft.scopes, scope) })}
                />
              </li>
            );
          })}
        </ul>
      </fieldset>

      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-semibold text-foreground">Which projects</legend>
        <Tabs
          chrome="inline"
          tabs={[
            { id: "all", label: "All my projects" },
            { id: "pick", label: "Only the ones I pick" },
          ]}
          activeTab={draft.mode}
          onTabChange={(id) => setDraft({ ...draft, mode: id as ProjectMode })}
        />
        {draft.mode === "all" ? (
          <p className="text-xs text-muted-foreground">
            Every team project you can see, now and later. Choose &ldquo;Only the ones I pick&rdquo; to limit it.
          </p>
        ) : tree === null ? (
          <p className="rounded-md border border-border p-3 text-xs text-muted-foreground">
            Metorite cannot list your projects now. Choose All my projects, or reload the page.
          </p>
        ) : (
          <AccessTree
            tree={tree}
            selection={draft.selection}
            onChange={(selection) => setDraft({ ...draft, selection })}
            disabled={saving}
          />
        )}
        {notice && (
          <p role="status" data-testid="stale-marks" className="text-xs text-warning">
            {notice}
          </p>
        )}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 text-sm">
            <span className="block text-foreground">My personal projects and notes</span>
            <span className="block text-xs text-muted-foreground">Off keeps them private from the agent.</span>
          </div>
          <Switch
            label="My personal projects and notes"
            checked={draft.includePersonal}
            disabled={saving}
            onChange={(on) => setDraft({ ...draft, includePersonal: on })}
          />
        </div>
      </fieldset>

      <p className="rounded-md bg-secondary/60 p-3 text-sm text-foreground" data-testid="change-summary">
        {summary.map((part, i) =>
          part.strong ? (
            <strong key={i} className="font-semibold">
              {part.text}
            </strong>
          ) : (
            <span key={i}>{part.text}</span>
          ),
        )}
      </p>
      {problem && problem !== TOO_LONG_NOTICE && (
        <p className="text-xs text-warning" data-testid="change-problem">
          {problem}
        </p>
      )}
      {error && (
        <p className="text-sm text-destructive" role="alert" data-testid="change-error">
          {error}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="secondary" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          icon="Check"
          loading={saving}
          disabled={!built.ok || unchanged}
          onClick={() => void save()}
        >
          Save changes
        </Button>
      </div>
    </div>
  );
}

/** Read the project list once, then draw the form. */
export default function AgentAccessPanel(props: Omit<AgentAccessFormProps, "tree">) {
  const [tree, setTree] = useState<ProjectTree | null | undefined>(undefined);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/projects/tree", { cache: "no-store", signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        setTree(buildProjectTree(await res.json()));
      })
      .catch(() => {
        if (!controller.signal.aborted) setTree(null);
      });
    return () => controller.abort();
  }, []);

  if (tree === undefined) {
    return (
      <div className="flex flex-col gap-2 border-t border-border pt-3" aria-busy="true" aria-label="Loading your projects">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }
  return <AgentAccessForm {...props} tree={tree} />;
}
