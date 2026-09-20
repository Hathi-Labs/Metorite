"use client";

/**
 * Projects · the repeat rule, in the task panel (WS-27o).
 *
 * **The sentence is the feature.** A form of five controls is a shape; a line
 * reading *"Every 2 weeks on Mon, Thu, keeping to the schedule"* is something
 * somebody can check before they commit to it — and it is shown live, not on
 * save, because the mistake this prevents (picking the wrong anchor) is
 * invisible until a cadence has drifted for three months.
 *
 * Saving is explicit. Every other control in this panel writes as you touch it,
 * but a repeat rule is a decision with a shape, and autosaving a half-built one
 * would push a weekly rule with no weekday at the server on every keystroke.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { useEffect, useState } from "react";

import { projectsApi } from "../lib/api";
import {
  ANCHORS,
  FREQS,
  type Freq,
  type Rule,
  WEEKDAY_LABELS,
  describeRule,
  emptyRule,
  ruleProblem,
  toPayload,
  toggleWeekday,
} from "../lib/recurrence";

const FREQ_LABELS: Record<Freq, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  yearly: "Yearly",
};

const ANCHOR_LABELS: Record<string, string> = {
  due: "Keep to the schedule",
  completed: "Measure from when it is finished",
};

interface Props {
  taskId: string;
}

export function RepeatEditor({ taskId }: Props) {
  const [saved, setSaved] = useState<Rule | null>(null);
  const [draft, setDraft] = useState<Rule | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    projectsApi
      .recurrence(taskId)
      .then((res) => {
        if (!live) return;
        setSaved(res.rule);
        setDraft(null);
      })
      // A panel that works without its repeat row beats one that refuses to
      // open because the row did not load.
      .catch(() => {
        if (live) setSaved(null);
      });
    return () => {
      live = false;
    };
  }, [taskId]);

  const editing = draft !== null;
  const problem = draft ? ruleProblem(draft) : null;

  async function save() {
    if (!draft || problem) return;
    setBusy(true);
    setError(null);
    try {
      const res = await projectsApi.setRecurrence(taskId, toPayload(draft));
      setSaved(res.rule);
      setDraft(null);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setError(null);
    try {
      await projectsApi.clearRecurrence(taskId);
      setSaved(null);
      setDraft(null);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const set = (patch: Partial<Rule>) =>
    setDraft((current) => ({ ...(current ?? emptyRule()), ...patch }));

  return (
    <div>
      <span className="text-xs text-muted-foreground">Repeats</span>

      {error ? (
        <p className="mt-1 rounded-md bg-muted px-2 py-1 text-xs text-foreground">
          {error}
        </p>
      ) : null}

      {!editing ? (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          {saved ? (
            <>
              <Badge tone="primary" icon="Repeat">
                on
              </Badge>
              <span className="min-w-0 flex-1 text-xs text-foreground">
                {describeRule(saved)}
              </span>
              <Button variant="ghost" size="sm" onClick={() => setDraft(saved)}>
                Change
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={busy}
                title="Stops the series. Everything it already created stays."
                onClick={() => void stop()}
              >
                Stop
              </Button>
            </>
          ) : (
            <>
              <span className="text-xs text-muted-foreground">
                Does not repeat.
              </span>
              <Button
                variant="ghost"
                size="sm"
                icon="Repeat"
                onClick={() => setDraft(emptyRule())}
              >
                Repeat…
              </Button>
            </>
          )}
        </div>
      ) : (
        <div className="mt-1 space-y-2 rounded-md border border-border p-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Every</span>
            <Input
              inputSize="sm"
              type="number"
              min={1}
              className="w-16"
              aria-label="Repeat interval"
              value={String(draft.interval)}
              onChange={(e) => set({ interval: Number(e.target.value) })}
            />
            <SelectButton
              label="How often"
              widthClass="w-[8rem]"
              value={draft.freq}
              onChange={(next) => set({ freq: next as Freq })}
              options={FREQS.map((f) => ({ value: f, label: FREQ_LABELS[f] }))}
            />
          </div>

          {draft.freq === "weekly" ? (
            <div className="flex flex-wrap gap-1">
              {WEEKDAY_LABELS.map(([n, label]) => {
                const on = draft.weekdays.includes(n);
                return (
                  <Button
                    key={n}
                    size="sm"
                    variant={on ? "primary" : "secondary"}
                    aria-pressed={on}
                    onClick={() => set({ weekdays: toggleWeekday(draft.weekdays, n) })}
                  >
                    {label}
                  </Button>
                );
              })}
            </div>
          ) : null}

          {draft.freq === "monthly" || draft.freq === "yearly" ? (
            <div className="flex flex-wrap items-center gap-2">
              {draft.freq === "yearly" ? (
                <SelectButton
                  label="Month"
                  widthClass="w-[9rem]"
                  value={String(draft.month_of_year ?? 1)}
                  onChange={(next) => set({ month_of_year: Number(next) })}
                  options={Array.from({ length: 12 }, (_, i) => ({
                    value: String(i + 1),
                    label: new Date(2026, i, 1).toLocaleString(undefined, {
                      month: "long",
                    }),
                  }))}
                />
              ) : null}
              <span className="text-xs text-muted-foreground">on day</span>
              <Input
                inputSize="sm"
                type="number"
                min={1}
                max={31}
                className="w-16"
                aria-label="Day of the month"
                value={String(draft.day_of_month ?? "")}
                onChange={(e) => set({ day_of_month: Number(e.target.value) })}
              />
              {/* Said out loud, because "the 31st" in February is the one thing
                  about a monthly rule that surprises people. */}
              {(draft.day_of_month ?? 0) > 28 ? (
                <span className="text-[11px] text-muted-foreground">
                  Shorter months use their last day.
                </span>
              ) : null}
            </div>
          ) : null}

          <SelectButton
            label="Measured from"
            widthClass="w-full"
            value={draft.anchor}
            onChange={(next) => set({ anchor: next as Rule["anchor"] })}
            options={ANCHORS.map((a) => ({ value: a, label: ANCHOR_LABELS[a] }))}
          />

          {/* Live, not on save: picking the wrong anchor is invisible until a
              cadence has drifted for three months. */}
          <p className="text-[11px] text-foreground">
            {problem ?? describeRule(draft)}
          </p>

          <div className="flex gap-1">
            <Button size="sm" loading={busy} disabled={Boolean(problem)} onClick={() => void save()}>
              Save
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDraft(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
