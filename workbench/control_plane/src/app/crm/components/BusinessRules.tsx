"use client";

/**
 * Settings → Business rules (WS-53 CRM-Q33, spec crm_platform.md §18.15).
 *
 * A rule is a short statement of how the business reads its numbers, such
 * as "All amounts net of GST". A CRM admin sets a rule for everyone, and each
 * member sets a rule for themselves. The AI follows each rule in its answers,
 * and a rule "net of GST" also moves the recommendation of an amount
 * question to the fields without GST.
 *
 * - **For everyone.** The org's rules. An admin changes and removes them.
 * - **For you only.** The caller's own rules.
 * - **Add a rule.** The words, and for an admin, who the rule is for.
 * - **Your choices in the chat.** What the caller picked on a question in
 *   the chat, which waits for a CRM admin. Clear goes back to the AI's
 *   choice (CRM-Q33 part 4).
 *
 * `lib/rules.ts` holds every word and rule, and this file only draws them.
 * The views take props only, so a test reads their markup.
 */

import Button from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import { SelectButton } from "@/components/ui/SelectButton";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { useCachedResource } from "@/lib/useCachedResource";
import { useState } from "react";

import * as api from "../lib/api";
import {
  ADD_HEADING,
  BODY_MAX,
  CANCEL_LABEL,
  CHANGE_LABEL,
  CHOICES_HEADING,
  CHOICES_NOTE,
  CLEARED_LINE,
  CLEAR_LABEL,
  MINE_HEADING,
  MY_CHOICES_KEY,
  NO_MY_RULES,
  NO_ORG_RULES,
  ORG_HEADING,
  REMOVED_LINE,
  REMOVE_LABEL,
  RULES_KEY,
  RULES_NOTE,
  RULES_TITLE,
  RULES_UNREAD,
  RULE_PLACEHOLDER,
  SAVED_LINE,
  SAVE_LABEL,
  SCOPE_LABEL,
  choiceLine,
  readRules,
  ruleByLine,
  ruleError,
  ruleProblem,
  rulesGate,
  scopeOptions,
  splitRules,
  type BusinessRule,
  type MyChoice,
  type RuleScope,
  type RulesGate,
  type RulesRead,
} from "../lib/rules";
import { Card } from "./SettingsCards";

// ── The read ───────────────────────────────────────────────────────────────

/**
 * The rules of the caller, for each member in Mirror mode. The page and the
 * section share this ONE read (`RULES_KEY`). A 404 `rules_off` reads as
 * `off`, and hides the section.
 */
export function useRules(enabled: boolean): {
  read: RulesRead | undefined;
  gate: RulesGate;
  failed: boolean;
  refresh: () => void;
} {
  const res = useCachedResource<RulesRead>(enabled ? RULES_KEY : null, () => readRules(api.listRules));
  const failed = enabled && res.data === undefined && res.error !== null;
  return {
    read: enabled ? res.data : undefined,
    gate: enabled ? rulesGate(res.data, failed) : "off",
    failed,
    refresh: res.refresh,
  };
}

// ── One rule ───────────────────────────────────────────────────────────────

export type RuleHandlers = {
  /** The id of the rule whose request runs, `add`, or `null`. */
  busy: string | null;
  onChange: (rule: BusinessRule, body: string) => Promise<boolean>;
  onRemove: (rule: BusinessRule) => void;
};

/** One rule: its words, who set it, and Change and Remove when the caller may. */
export function RuleRow({ rule, handlers, now }: { rule: BusinessRule; handlers: RuleHandlers; now?: Date }) {
  const [draft, setDraft] = useState<string | null>(null);
  const disabled = handlers.busy !== null;
  const problem = draft === null ? null : ruleProblem(draft);
  return (
    <li className="space-y-1.5 px-4 py-3" data-rule={rule.scope}>
      {draft === null ? (
        <p className="break-words text-xs text-foreground">{rule.body}</p>
      ) : (
        <Textarea
          aria-label={`${CHANGE_LABEL}: ${rule.body}`}
          value={draft}
          maxLength={BODY_MAX}
          rows={2}
          onChange={(e) => setDraft(e.target.value)}
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-[11px] text-muted-foreground">{ruleByLine(rule, now)}</span>
        {rule.editable && draft === null ? (
          <>
            <Button variant="ghost" size="sm" disabled={disabled} onClick={() => setDraft(rule.body)}>
              {CHANGE_LABEL}
            </Button>
            <Button variant="ghost" size="sm" disabled={disabled} onClick={() => handlers.onRemove(rule)}>
              {REMOVE_LABEL}
            </Button>
          </>
        ) : null}
        {draft !== null ? (
          <>
            <Button variant="ghost" size="sm" disabled={disabled} onClick={() => setDraft(null)}>
              {CANCEL_LABEL}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={disabled || problem !== null}
              loading={handlers.busy === rule.id}
              onClick={() => void handlers.onChange(rule, draft).then((ok) => ok && setDraft(null))}
            >
              {SAVE_LABEL}
            </Button>
          </>
        ) : null}
      </div>
    </li>
  );
}

function RuleList({
  heading,
  empty,
  rules,
  handlers,
  now,
}: {
  heading: string;
  empty: string;
  rules: BusinessRule[];
  handlers: RuleHandlers;
  now?: Date;
}) {
  return (
    <div className="border-b border-border/60">
      <h3 className="px-4 pt-3 text-[11px] font-medium text-muted-foreground">{heading}</h3>
      {rules.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">{empty}</p>
      ) : (
        <ul className="divide-y divide-border/60" aria-label={heading}>
          {rules.map((rule) => (
            <RuleRow key={rule.id} rule={rule} handlers={handlers} now={now} />
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Add a rule ─────────────────────────────────────────────────────────────

export function AddRule({
  canAdmin,
  busy,
  onAdd,
}: {
  canAdmin: boolean;
  busy: boolean;
  onAdd: (body: string, scope: RuleScope) => Promise<boolean>;
}) {
  const [body, setBody] = useState("");
  const [scope, setScope] = useState<RuleScope>("me");
  const options = scopeOptions(canAdmin);
  const problem = body === "" ? null : ruleProblem(body);
  return (
    <div className="space-y-2 px-4 py-3">
      <h3 className="text-[11px] font-medium text-muted-foreground">{ADD_HEADING}</h3>
      <Textarea
        aria-label={ADD_HEADING}
        placeholder={RULE_PLACEHOLDER}
        value={body}
        maxLength={BODY_MAX}
        rows={2}
        onChange={(e) => setBody(e.target.value)}
      />
      {problem ? <p className="text-[11px] text-destructive">{problem}</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        {options.length > 1 ? (
          <SelectButton
            label={SCOPE_LABEL}
            value={scope}
            options={options}
            widthClass="w-full sm:w-40"
            disabled={busy}
            onChange={(value) => setScope(value === "org" ? "org" : "me")}
          />
        ) : null}
        <Button
          variant="primary"
          size="sm"
          icon="Plus"
          disabled={busy || ruleProblem(body) !== null}
          loading={busy}
          onClick={() => void onAdd(body, scope).then((ok) => ok && setBody(""))}
        >
          {SAVE_LABEL}
        </Button>
      </div>
    </div>
  );
}

// ── Your choices in the chat ───────────────────────────────────────────────

export function MyChoices({
  choices,
  busy,
  onClear,
  now,
}: {
  choices: MyChoice[];
  busy: string | null;
  onClear: (choice: MyChoice) => void;
  now?: Date;
}) {
  if (choices.length === 0) return null;
  return (
    <div className="border-t border-border px-4 py-3">
      <h3 className="text-[11px] font-medium text-muted-foreground">{CHOICES_HEADING}</h3>
      <p className="text-[11px] text-muted-foreground">{CHOICES_NOTE}</p>
      <ul className="mt-2 space-y-2" aria-label={CHOICES_HEADING}>
        {choices.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <div className="min-w-0 flex-1">
              <p className="break-words text-xs text-foreground">{c.question}</p>
              <p className="break-words text-[11px] text-muted-foreground">{choiceLine(c, now)}</p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy !== null}
              loading={busy === c.id}
              onClick={() => onClear(c)}
            >
              {CLEAR_LABEL}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── The section ────────────────────────────────────────────────────────────

/** The section, drawn from the reads that the caller holds. No hook, so a test reads its markup. */
export function BusinessRulesView({
  read,
  failed,
  onRetry,
  handlers,
  onAdd,
  choices,
  onClear,
  now,
}: {
  read: RulesRead | undefined;
  failed: boolean;
  onRetry: () => void;
  handlers: RuleHandlers;
  onAdd: (body: string, scope: RuleScope) => Promise<boolean>;
  choices: MyChoice[];
  onClear: (choice: MyChoice) => void;
  now?: Date;
}) {
  if (read?.off) return null;
  if (read === undefined) {
    return (
      <Card title={RULES_TITLE} note={RULES_NOTE}>
        {failed ? (
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            <p className="text-xs text-muted-foreground">{RULES_UNREAD}</p>
            <Button variant="secondary" size="sm" onClick={onRetry}>
              Try again
            </Button>
          </div>
        ) : (
          <div className="space-y-2 px-4 py-3">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-4 w-40" />
          </div>
        )}
      </Card>
    );
  }
  const { org, mine } = splitRules(read.body.rules);
  return (
    <Card title={RULES_TITLE} note={RULES_NOTE}>
      <RuleList heading={ORG_HEADING} empty={NO_ORG_RULES} rules={org} handlers={handlers} now={now} />
      <RuleList heading={MINE_HEADING} empty={NO_MY_RULES} rules={mine} handlers={handlers} now={now} />
      <AddRule canAdmin={read.body.can_admin} busy={handlers.busy === "add"} onAdd={onAdd} />
      <MyChoices choices={choices} busy={handlers.busy} onClear={onClear} now={now} />
    </Card>
  );
}

/** Settings → Business rules. The page draws it for each member in Mirror mode, while the gate is on. */
export default function BusinessRulesCard() {
  const toast = useToast();
  const { read, failed, refresh } = useRules(true);
  const mine = useCachedResource<{ choices: MyChoice[] }>(MY_CHOICES_KEY, () => api.listMyChoices());
  const [busy, setBusy] = useState<string | null>(null);

  async function run(key: string, work: () => Promise<unknown>, done: string, fallback: string) {
    setBusy(key);
    try {
      await work();
      toast.show({ key: `crm-rule:${key}`, variant: "success", title: done });
      return true;
    } catch (err) {
      toast.show({ key: `crm-rule:${key}`, variant: "error", title: ruleError(err, fallback) });
      return false;
    } finally {
      setBusy(null);
    }
  }

  return (
    <BusinessRulesView
      read={read}
      failed={failed}
      onRetry={refresh}
      handlers={{
        busy,
        onChange: (rule, body) =>
          run(rule.id, () => api.changeRule(rule.id, body), SAVED_LINE, "That change did not save."),
        onRemove: (rule) =>
          void run(rule.id, () => api.removeRule(rule.id), REMOVED_LINE, "That rule was not removed."),
      }}
      onAdd={(body, scope) => run("add", () => api.addRule(body, scope), SAVED_LINE, "That rule did not save.")}
      choices={mine.data?.choices ?? []}
      onClear={(choice) =>
        void run(choice.id, () => api.clearMyChoice(choice.id), CLEARED_LINE, "That choice was not cleared.")
      }
    />
  );
}
