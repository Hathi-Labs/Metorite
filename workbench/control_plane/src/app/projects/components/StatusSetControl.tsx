"use client";

/**
 * Projects · where a project's statuses come from (migration 196).
 *
 * Owner directive 2026-09-06. It sits at the top of the status editor and is
 * the whole of the new model's surface:
 *
 *     ( ) Inherit from Product Engineering
 *     (o) Use its own statuses          [ Copy from… ]
 *
 * The model behind it is one sentence — *a project uses the status set of the
 * nearest node at or above it that owns one* — and this control is deliberately
 * the only place it is stated, because a control that says it is better than a
 * paragraph explaining it. Three sentences of prose came OUT of the editor when
 * this went in.
 *
 * ## Why the mapping is inline and not a second dialog
 *
 * Changing which set applies moves tasks between lanes, and the person doing it
 * must see where they land before they agree. That could be a modal over a
 * modal; it is a panel in the same dialog instead, because the lane list behind
 * it is the context that makes the choice readable — you are choosing between
 * two lists, and hiding one of them under a sheet is how the choice gets made
 * blind.
 *
 * ## Two things it will not do
 *
 * **It will not confirm while a row is unset.** The automatic rule fills every
 * row it can (exact name, then the first lane of the same stage), so a blank is
 * a case the rule genuinely could not decide. Guessing there is silent and
 * permanent, and the whole point of asking is to not guess.
 *
 * **It will not offer itself without the permission.** `may_edit` carries
 * `projects:settings:write`. Without it the control renders as a sentence
 * naming what this project uses and who to ask — an act you cannot perform
 * should not look like one you have not tried.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Select } from "@/components/ui/Input";
import { useEffect, useMemo, useState } from "react";

import {
  type ProjectRow,
  type StatusSetChange,
  type StatusSetInfo,
  type StatusSetPreview,
  projectsApi,
} from "../lib/api";

interface Props {
  projectId: string;
  /** Refetch the lanes — the set they came from has changed underneath. */
  onSwitched: (summary: string) => void;
  onError: (message: string) => void;
  busy: boolean;
}

export function StatusSetControl({
  projectId,
  onSwitched,
  onError,
  busy,
}: Props) {
  const [info, setInfo] = useState<StatusSetInfo | null>(null);
  /** The change being considered, and what it would do. Null = nothing asked. */
  const [pending, setPending] = useState<StatusSetChange | null>(null);
  const [preview, setPreview] = useState<StatusSetPreview | null>(null);
  /** `{old status id: target lane NAME}` — see `projectsApi.setStatusSet`. */
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [working, setWorking] = useState(false);
  const [sources, setSources] = useState<ProjectRow[]>([]);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const [set, tree] = await Promise.all([
          projectsApi.statusSet(projectId),
          projectsApi.tree(),
        ]);
        if (!live) return;
        setInfo(set);
        // Anything but this node — copying a set from yourself is a no-op the
        // picker should not offer as though it were a choice.
        setSources(tree.rows.filter((r) => r.id !== projectId));
      } catch (err) {
        if (live) onError(String((err as Error).message));
      }
    })();
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  /** Rows the human actually has to answer: they carry tasks and they move. */
  const questions = useMemo(
    () => (preview?.moves ?? []).filter((m) => m.tasks > 0 && !m.unchanged),
    [preview]
  );
  const unanswered = questions.filter((m) => !choices[m.status_id]);

  async function ask(change: StatusSetChange) {
    setWorking(true);
    try {
      const seen = await projectsApi.previewStatusSet(projectId, change);
      // Nothing to move: no card, no confirm. There is nothing to decide, and
      // asking anyway teaches people to click through the card without reading.
      if (seen.moving === 0) {
        await apply(change);
        return;
      }
      setPending(change);
      setPreview(seen);
      setChoices(
        Object.fromEntries(
          seen.moves
            .filter((m) => m.tasks > 0 && !m.unchanged && m.suggested)
            .map((m) => [m.status_id, m.suggested as string])
        )
      );
    } catch (err) {
      onError(String((err as Error).message));
    } finally {
      setWorking(false);
    }
  }

  async function apply(change: StatusSetChange) {
    setWorking(true);
    try {
      const done = await projectsApi.setStatusSet(projectId, {
        ...change,
        mapping: change.mapping ?? choices,
      });
      setPending(null);
      setPreview(null);
      setChoices({});
      setInfo(await projectsApi.statusSet(projectId));
      onSwitched(
        done.moved
          ? `Switched, and moved ${done.moved} task(s).` +
              (done.completed ? ` ${done.completed} now count as complete.` : "") +
              (done.reopened ? ` ${done.reopened} are open again.` : "")
          : "Switched. Nothing needed moving."
      );
    } catch (err) {
      onError(String((err as Error).message));
    } finally {
      setWorking(false);
    }
  }

  if (!info) return null;

  // Read-only, and it says which permission and who grants it. An admin can
  // act on that sentence; "you cannot do this" cannot be acted on at all.
  if (!info.may_edit) {
    return (
      <p className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
        Statuses come from{" "}
        <span className="font-medium text-foreground">{info.owner_name}</span>.
        Changing them needs the{" "}
        <code className="font-mono">projects:settings:write</code> permission —
        ask an organization admin.
      </p>
    );
  }

  const disabled = busy || working;

  return (
    <div className="border-b border-border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <Choice
          on={!info.owns}
          disabled={disabled || !info.can_inherit}
          label={
            info.can_inherit
              ? `Inherit from ${info.owner_name}`
              : "Inherit from a parent"
          }
          // A space has nothing above it, so this is not a choice it can make.
          // Disabled and explained, never hidden — an absent option reads as a
          // missing feature rather than an impossible one.
          hint={
            info.can_inherit
              ? undefined
              : "A space has nothing above it to inherit from."
          }
          onPick={() => void ask({ mode: "inherit" })}
        />
        <Choice
          on={info.owns}
          disabled={disabled}
          label="Use its own statuses"
          hint={
            info.owns
              ? undefined
              : info.has_dormant_set
                ? "Its previous lanes are still here."
                : undefined
          }
          onPick={() => void ask({ mode: "own" })}
        />

        {info.owns ? (
          <div className="ml-auto w-52">
            <Select
              inputSize="sm"
              aria-label="Copy statuses from another project"
              value=""
              disabled={disabled}
              onChange={(e) => {
                if (!e.target.value) return;
                void ask({ mode: "own", copy_from: e.target.value });
              }}
            >
              <option value="">Copy from…</option>
              {sources.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
      </div>

      {preview && pending ? (
        <div className="mt-3 rounded-lg border border-border">
          <p className="border-b border-border px-3 py-2 text-xs font-medium text-foreground">
            Move {preview.moving} task(s) into the new statuses
          </p>
          <div className="max-h-56 overflow-y-auto">
            <table className="w-full text-xs">
              <tbody>
                {questions.map((m) => (
                  <tr key={m.status_id} className="border-b border-border/60">
                    <td className="px-3 py-1.5 text-foreground">{m.name}</td>
                    <td className="px-2 py-1.5 tabular-nums text-muted-foreground">
                      {m.tasks}
                    </td>
                    <td className="py-1 pr-3">
                      <Select
                        inputSize="sm"
                        aria-label={`Where ${m.name} lands`}
                        value={choices[m.status_id] ?? ""}
                        onChange={(e) =>
                          setChoices((c) => ({
                            ...c,
                            [m.status_id]: e.target.value,
                          }))
                        }
                      >
                        <option value="">Pick a status…</option>
                        {preview.lanes.map((lane) => (
                          <option key={lane.id} value={lane.name}>
                            {lane.name}
                          </option>
                        ))}
                      </Select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* The one effect that reaches outside Projects, said before the
              click rather than discovered afterwards. */}
          {preview.completing || preview.reopening ? (
            <p className="flex items-start gap-1.5 border-t border-border px-3 py-2 text-xs text-foreground">
              <Icon name="TriangleAlert" className="mt-0.5 h-3 w-3 shrink-0" />
              <span>
                {preview.completing
                  ? `${preview.completing} task(s) will be marked complete.`
                  : ""}
                {preview.completing && preview.reopening ? " " : ""}
                {preview.reopening
                  ? `${preview.reopening} will be re-opened.`
                  : ""}
              </span>
            </p>
          ) : null}

          <div className="flex items-center gap-2 border-t border-border px-3 py-2">
            <Button
              variant="primary"
              size="sm"
              disabled={disabled || unanswered.length > 0}
              // Named, so a disabled button is never a mystery.
              title={
                unanswered.length
                  ? `Still to answer: ${unanswered
                      .map((m) => m.name)
                      .join(", ")}`
                  : undefined
              }
              onClick={() => void apply(pending)}
            >
              Move and switch
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => {
                setPending(null);
                setPreview(null);
                setChoices({});
              }}
            >
              Cancel
            </Button>
            {unanswered.length ? (
              <span className="text-[11px] text-muted-foreground">
                {unanswered.length} still to answer
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** A radio, in the house's own parts rather than a bare `<input>`. */
function Choice({
  on,
  label,
  hint,
  disabled,
  onPick,
}: {
  on: boolean;
  label: string;
  hint?: string;
  disabled: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      disabled={disabled || on}
      title={hint}
      onClick={onPick}
      className={`flex items-center gap-2 text-xs ${
        disabled && !on
          ? "cursor-not-allowed text-muted-foreground/50"
          : "text-foreground"
      }`}
    >
      <span
        className={`h-3.5 w-3.5 shrink-0 rounded-full border ${
          on ? "border-primary bg-primary/20 ring-2 ring-inset ring-primary" : "border-border"
        }`}
      />
      <span>{label}</span>
    </button>
  );
}

export default StatusSetControl;
