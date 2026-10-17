"use client";

/**
 * The Field map card of the CRM settings page (WS-53 CRM-Q7a, spec section 18.7).
 *
 * For each entity it lists the Zoho fields that go to `custom` today. For each
 * field it shows the rule, if one exists: the target column, the status, who
 * proposed it and why. An admin can approve or reject a proposal, reject an
 * approved rule, or pick a target by hand. The next sync applies the choice.
 *
 * It is for an admin in Mirror mode only. The page does not draw it in Native
 * mode. The gateway decides which columns a field may fill (`targets`), so
 * this card offers only those.
 *
 * CRM-Q20-2 removed "Suggest mappings" (CRM-Q7b). The mapping pipeline
 * writes its suggestion into the open draft, and the draft draws each of its
 * items. A proposal from before still takes approve and reject, and shows no
 * confidence number.
 *
 * **The plan (CRM-Q18-2, spec section 18.9.6).** The top of the card names
 * the field map in use, who approved it and when. A pick by hand ("Map") goes
 * into a draft (`POST /crm/mapping/plans`) and changes no live rule. The card
 * shows the draft as not applied, with its changes against the map in use,
 * and "Approve draft" applies it in one press. "Go back" asks first, then
 * approves the content of the map before. The history lists each version,
 * and a version opens its rules, read only. The approve and the reject of one
 * rule stay as they were, and the gateway writes a version for each one.
 * `lib/fieldMap.ts` decides each of these, and this file only draws them.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import SelectButton from "@/components/ui/SelectButton";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";
import { useState } from "react";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import {
  ADDED_TO_DRAFT,
  APPROVED_DONE,
  APPROVE_DRAFT_LABEL,
  DRAFT_LABEL,
  DRAFT_NOTE,
  FIELD_MAP_NOTE,
  FIELD_MAP_TITLE,
  GO_BACK_LABEL,
  GO_BACK_TITLE,
  HISTORY_LABEL,
  NO_DRAFT_CHANGE,
  NO_FIELDS,
  NO_TARGET,
  PLAN_TITLE,
  PLAN_UNREAD,
  STALE_SENTENCE,
  WENT_BACK,
  actionsFor,
  addToDraft,
  approveDraft,
  approvedLine,
  approvedSummary,
  columnLabel,
  diffLine,
  goBack,
  goBackBody,
  historyLine,
  kindDiff,
  kindLine,
  openDraft,
  pickOptions,
  planDiff,
  planRuleLine,
  planRules,
  previousVersion,
  ruleCountLabel,
  ruleFor,
  ruleLine,
  stateLabel,
  stateTone,
  statusLabel,
  statusTone,
  type FieldMap,
  type FieldMapField,
  type FieldRule,
  type MappingPlan,
  type PlanCalls,
  type PlanList,
  type PlanOutcome,
} from "../lib/fieldMap";
import type { EntitySlug } from "../lib/types";
import { FIELD_ENTITIES } from "./FieldsCard";
import { Card } from "./SettingsCards";

const KEY_PREFIX = "crm:field-map:";

export type FieldMapHandlers = {
  /** The key of the field whose request runs, or `null`. */
  busy: string | null;
  onPick: (entity: EntitySlug, field: string, column: string) => void;
  onDecide: (rule: FieldRule, status: "approved" | "rejected") => void;
  /**
   * CRM-Q18-2. True while the card cannot build a draft yet: the plan, or
   * the rules of the open draft, still load. "Map" waits.
   */
  pickWaits?: boolean;
};

/** What the plan part of the card draws (CRM-Q18-2). */
export type PlanView = {
  /** `GET /crm/mapping/plans`, or `null` while it loads or after it failed. */
  list: PlanList | null;
  /** True when the read of the list failed. */
  failed?: boolean;
  /** The rules of the version in use, for the changes of the draft. */
  approved: MappingPlan | null;
  /** The rules of the open draft. */
  draft: MappingPlan | null;
  /** The version whose rules the history shows, and its rules. */
  opened: number | null;
  openedPlan: MappingPlan | null;
  /** The write that runs. */
  busy: "approve" | "restore" | null;
  /** The sentence after a write that did not apply. */
  notice: string | null;
};

export type PlanHandlers = {
  onApprove: (version: number) => void;
  /** Opens the question. It does not go back by itself. */
  onGoBack: () => void;
  /** Open the rules of one version, or close them with `null`. */
  onOpen: (version: number | null) => void;
};

/** The rules of one version, read only. */
function PlanRuleList({ plan }: { plan: MappingPlan | null }) {
  if (plan === null) return <Skeleton className="h-3 w-40" />;
  const rules = planRules(plan);
  if (rules.length === 0) {
    return <p className="text-[11px] text-muted-foreground">No field is mapped.</p>;
  }
  return (
    <ul className="space-y-0.5">
      {rules.map((rule) => (
        <li key={`${rule.entity}:${rule.field}`} className="text-[11px] text-foreground">
          {planRuleLine(rule)}
        </li>
      ))}
    </ul>
  );
}

/**
 * The plan part of the card: the map in use, the open draft and the history.
 * It holds no state and no hook, so a test calls it for its element tree.
 */
export function PlanPanel({ view, handlers }: { view: PlanView; handlers: PlanHandlers }) {
  const { list } = view;
  if (list === null) {
    return (
      <div aria-label={PLAN_TITLE} className="border-b border-border px-4 py-3">
        {view.failed ? (
          <p className="text-xs text-muted-foreground">{PLAN_UNREAD}</p>
        ) : (
          <Skeleton className="h-4 w-56" />
        )}
      </div>
    );
  }
  const inUse = approvedSummary(list);
  const draft = openDraft(list);
  const previous = previousVersion(list);
  const changes = planDiff(view.approved, view.draft);
  // Batch M review P1: the items of a suggestion draw too, so the one press
  // never applies an item that the admin cannot see.
  const kinds = kindDiff(view.approved, view.draft);
  const writing = view.busy !== null;
  return (
    <div aria-label={PLAN_TITLE} className="space-y-3 border-b border-border px-4 py-3">
      {view.notice && (
        <p role="alert" className="text-[11px] text-warning">
          {view.notice}
        </p>
      )}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-0.5">
          <h3 className="text-xs font-medium text-foreground">{PLAN_TITLE}</h3>
          <p className="text-[11px] text-muted-foreground">
            {inUse ? approvedLine(inUse) : "No field map is approved yet."}
            {inUse ? ` ${ruleCountLabel(inUse.rule_count)}.` : ""}
          </p>
        </div>
        {previous && (
          <Button
            size="sm"
            variant="ghost"
            icon="Undo2"
            loading={view.busy === "restore"}
            disabled={writing}
            onClick={handlers.onGoBack}
          >
            {GO_BACK_LABEL}
          </Button>
        )}
      </div>
      {draft && (
        <div
          aria-label={DRAFT_LABEL}
          className="space-y-2 rounded-lg border border-warning/40 bg-warning/5 p-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="warning" size="xs">
              {DRAFT_LABEL}
            </Badge>
            <span className="text-[11px] text-muted-foreground">{DRAFT_NOTE}</span>
          </div>
          {view.draft === null ? (
            <Skeleton className="h-3 w-48" />
          ) : changes.length === 0 && kinds.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              This draft changes nothing against the map in use.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {kinds.map((row) => (
                <li key={`kind:${kindLine(row)}`} className="text-xs text-foreground">
                  {kindLine(row)}
                </li>
              ))}
              {changes.map((row) => (
                <li key={`${row.entity}:${row.field}`} className="text-xs text-foreground">
                  {diffLine(row)}
                </li>
              ))}
            </ul>
          )}
          <Button
            size="sm"
            variant="primary"
            icon="Check"
            loading={view.busy === "approve"}
            disabled={writing || view.draft === null}
            onClick={() => handlers.onApprove(draft.version)}
          >
            {APPROVE_DRAFT_LABEL}
          </Button>
        </div>
      )}
      <CollapsibleSection
        label={HISTORY_LABEL}
        icon="History"
        count={list.versions.length}
        defaultOpen={false}
        keepMounted
      >
        <ul className="divide-y divide-border/40">
          {list.versions.map((v) => {
            const open = view.opened === v.version;
            return (
              <li key={v.version} className="space-y-1.5 py-2">
                <div className="flex items-start gap-2 text-[11px]">
                  <Badge tone={stateTone(v, draft)} size="xs">
                    {stateLabel(v, draft)}
                  </Badge>
                  {/* One text run, so a narrow card wraps it as a sentence. */}
                  <span className="min-w-0 flex-1 break-words text-muted-foreground">
                    {historyLine(v)} · {ruleCountLabel(v.rule_count)}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="shrink-0"
                    aria-expanded={open}
                    onClick={() => handlers.onOpen(open ? null : v.version)}
                  >
                    {open ? "Hide rules" : "Show rules"}
                  </Button>
                </div>
                {open && <PlanRuleList plan={view.openedPlan} />}
              </li>
            );
          })}
        </ul>
      </CollapsibleSection>
    </div>
  );
}

/** One field: its type, its rule, and the actions on it. */
function FieldRow({
  entity,
  field,
  rule,
  handlers,
}: {
  entity: EntitySlug;
  field: FieldMapField;
  rule: FieldRule | null;
  handlers: FieldMapHandlers;
}) {
  const [column, setColumn] = useState("");
  const actions = actionsFor(rule, field);
  const busy = handlers.busy === `${entity}:${field.key}`;
  return (
    <li className="space-y-1.5 py-2" aria-label={field.label}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-foreground">{field.label}</span>
        <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-muted-foreground">
          {field.type}
        </span>
        {rule && (
          <>
            <span className="text-muted-foreground">to</span>
            <span className="text-foreground">{columnLabel(rule.target_column)}</span>
            <Badge tone={statusTone(rule.status)} size="xs">
              {statusLabel(rule.status)}
            </Badge>
          </>
        )}
      </div>
      {rule && <p className="text-[11px] text-muted-foreground">{ruleLine(rule)}</p>}
      {rule?.status === "stale" && (
        <p className="text-[11px] text-warning">{STALE_SENTENCE}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {actions.includes("approve") && rule && (
          <Button
            size="sm"
            variant="secondary"
            loading={busy}
            disabled={busy}
            onClick={() => handlers.onDecide(rule, "approved")}
          >
            Approve
          </Button>
        )}
        {actions.includes("reject") && rule && (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => handlers.onDecide(rule, "rejected")}
          >
            Reject
          </Button>
        )}
        {actions.includes("pick") && (
          <>
            <SelectButton
              label="Target column"
              prompt="Pick a target"
              value={column}
              defaultValue=""
              options={pickOptions(field)}
              onChange={setColumn}
              widthClass="w-48"
              disabled={busy}
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={busy || !column || handlers.pickWaits === true}
              loading={busy && !rule}
              onClick={() => handlers.onPick(entity, field.key, column)}
            >
              Map
            </Button>
          </>
        )}
        {actions.length === 0 && (
          <span className="text-[11px] text-muted-foreground">{NO_TARGET}</span>
        )}
      </div>
    </li>
  );
}

/** The card, drawn from maps that the caller already holds. */
export function FieldMapCardView({
  maps,
  handlers,
  plan,
  planHandlers,
}: {
  maps: Record<EntitySlug, FieldMap | null>;
  handlers: FieldMapHandlers;
  /** CRM-Q18-2. Without it the card draws no plan part. */
  plan?: PlanView;
  planHandlers?: PlanHandlers;
}) {
  return (
    <Card title={FIELD_MAP_TITLE} note={FIELD_MAP_NOTE}>
      {plan && planHandlers && <PlanPanel view={plan} handlers={planHandlers} />}
      <div className="divide-y divide-border/60">
        {FIELD_ENTITIES.map(({ slug, label }) => {
          const map = maps[slug];
          return (
            <div key={slug} aria-label={label} className="px-4 py-3">
              <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
                {label}
              </h3>
              {map === null ? (
                <p className="pt-2 text-xs text-muted-foreground">Loading.</p>
              ) : map.fields.length === 0 ? (
                <p className="pt-2 text-xs text-muted-foreground">{NO_FIELDS}</p>
              ) : (
                <ul className="divide-y divide-border/40">
                  {map.fields.map((field) => (
                    <FieldRow
                      key={field.key}
                      entity={slug}
                      field={field}
                      rule={ruleFor(map, field.key)}
                      handlers={handlers}
                    />
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function useFieldMap(entity: EntitySlug): FieldMap | null {
  const res = useCachedResource<FieldMap | null>(`${KEY_PREFIX}${entity}`, () =>
    api.getFieldMap(entity).catch(() => null),
  );
  return res.data ?? null;
}

const PLANS_KEY = `${KEY_PREFIX}plans`;

/** One version with its rules, or `null` with no version. */
function usePlan(version: number | null): MappingPlan | null {
  const res = useCachedResource<MappingPlan | null>(
    version === null ? null : `${KEY_PREFIX}plan:${version}`,
    () => (version === null ? Promise.resolve(null) : api.getMappingPlan(version)),
  );
  return res.data ?? null;
}

/** The calls of the plan. A write reads the card again, whatever its result. */
const PLAN_CALLS: PlanCalls = {
  draft: api.draftMappingPlan,
  approve: api.approveMappingPlan,
  restore: api.restoreMappingPlan,
  reread: () => invalidate(KEY_PREFIX),
};

/** The card with its data. The page draws it for an admin in Mirror mode. */
export default function FieldMapCard() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const leads = useFieldMap("leads");
  const contacts = useFieldMap("contacts");
  const organizations = useFieldMap("organizations");
  const deals = useFieldMap("deals");

  // CRM-Q18-2: the plan.
  const plans = useCachedResource<PlanList>(PLANS_KEY, () => api.listMappingPlans());
  const list = plans.data ?? null;
  const inUse = approvedSummary(list);
  const draftSummary = openDraft(list);
  const approvedPlan = usePlan(inUse?.version ?? null);
  const draftPlan = usePlan(draftSummary?.version ?? null);
  const [opened, setOpened] = useState<number | null>(null);
  const openedPlan = usePlan(opened);
  const [planBusy, setPlanBusy] = useState<"approve" | "restore" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const previous = previousVersion(list);
  // A new draft carries the changes of the open draft, so a pick waits for
  // its rules. With no open draft, the gateway starts from the map in use.
  const pickWaits = list === null || (draftSummary !== null && draftPlan === null);

  async function run(key: string, act: () => Promise<unknown>, done: string | (() => string)) {
    setBusy(key);
    try {
      await act();
      const title = typeof done === "function" ? done() : done;
      toast.show({ key: "crm-field-map", variant: "success", title });
    } catch (err) {
      const message = err instanceof CrmError ? err.message : "The change did not save.";
      toast.show({ key: "crm-field-map", variant: "error", title: message });
    } finally {
      setBusy(null);
      invalidate(KEY_PREFIX);
    }
  }

  /** The toast or the notice of one write of the plan. */
  function report(outcome: PlanOutcome, done: string) {
    if (outcome.kind === "done") {
      setNotice(null);
      toast.show({ key: "crm-field-map", variant: "success", title: done });
      return;
    }
    // "moved" and "busy" keep their sentence on the card, because the admin
    // must act on it. The read after the write drops a moved draft.
    setNotice(outcome.message);
    if (outcome.kind === "failed") {
      toast.show({ key: "crm-field-map", variant: "error", title: outcome.message });
    }
  }

  async function approve(version: number) {
    setPlanBusy("approve");
    try {
      report(await approveDraft(PLAN_CALLS, version), APPROVED_DONE);
    } finally {
      setPlanBusy(null);
    }
  }

  async function restore() {
    setConfirming(false);
    if (previous === null) return;
    setPlanBusy("restore");
    try {
      report(await goBack(PLAN_CALLS, previous), WENT_BACK);
    } finally {
      setPlanBusy(null);
    }
  }

  const plan: PlanView = {
    list,
    failed: list === null && plans.error !== null,
    approved: approvedPlan,
    draft: draftPlan,
    opened,
    openedPlan,
    busy: planBusy,
    notice,
  };
  const planHandlers: PlanHandlers = {
    onApprove: (version) => void approve(version),
    onGoBack: () => setConfirming(true),
    onOpen: setOpened,
  };

  const handlers: FieldMapHandlers = {
    busy,
    pickWaits,
    // CRM-Q18-2: a pick goes into a draft, and no live rule changes.
    onPick: (entity, field, column) => {
      setNotice(null);
      let said = ADDED_TO_DRAFT;
      return run(
        `${entity}:${field}`,
        async () => {
          const result = await addToDraft(PLAN_CALLS, approvedPlan, draftPlan, {
            op: "put",
            entity: slugEntity(entity),
            field_api_name: field,
            target_column: column,
          });
          if (result === "unchanged") said = NO_DRAFT_CHANGE;
        },
        // `run` reads the sentence after the act, so the "unchanged" case shows.
        () => said,
      );
    },
    onDecide: (rule, status) => {
      const entity =
        FIELD_ENTITIES.find(({ slug }) => slugEntity(slug) === rule.entity)?.slug ?? "leads";
      return run(
        `${entity}:${rule.field_api_name}`,
        () => api.decideFieldRule(rule.id, status),
        status === "approved"
          ? "Approved. The next sync fills the column."
          : "Rejected. The next sync puts the column back.",
      );
    },
  };

  return (
    <>
      <FieldMapCardView
        maps={{ leads, contacts, organizations, deals }}
        handlers={handlers}
        plan={plan}
        planHandlers={planHandlers}
      />
      {previous && (
        <ConfirmDialog
          open={confirming}
          title={GO_BACK_TITLE}
          body={goBackBody(previous)}
          confirmLabel={GO_BACK_LABEL}
          confirmVariant="primary"
          icon="Undo2"
          onConfirm={() => void restore()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  );
}

/** The stored entity word of an API slug. */
function slugEntity(slug: EntitySlug): string {
  return slug === "organizations" ? "company" : slug.replace(/s$/, "");
}
