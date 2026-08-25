"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useState } from "react";
import {
  type TaskSettings,
  type EnergyWindow,
  type DayTemplate,
} from "@/app/tasks/lib/api";
import { DEFAULT_PLANNING_PROMPT, hourLabel } from "./shared";

const DOW_LABELS = ["S", "M", "T", "W", "T", "F", "S"]; // 0=Sun … 6=Sat

// A clock-hour dropdown ("7 AM", "12 PM", "10 PM") — far clearer + more
// touch-friendly than a bare 0–23 number box for setting times.
function HourSelect({
  value,
  onChange,
  min = 0,
  max = 24,
  fallback,
}: {
  value: number | undefined;
  onChange: (h: number) => void;
  min?: number;
  max?: number;
  fallback: number;
}) {
  const opts: number[] = [];
  for (let h = min; h <= max; h++) opts.push(h);
  return (
    <select
      value={value ?? fallback}
      onChange={(e) => onChange(parseInt(e.target.value, 10))}
      className="rounded border border-border bg-background px-1.5 py-1 text-foreground focus:border-primary/50 focus:outline-none"
    >
      {opts.map((h) => (
        <option key={h} value={h}>
          {hourLabel(h)}
        </option>
      ))}
    </select>
  );
}


// ── Calendar settings — bottom sheet on mobile, popover on desktop ───────────
export function CalendarSettings({
  settings,
  onChange,
  onClose,
}: {
  settings: TaskSettings;
  onChange: (patch: Partial<TaskSettings>) => void;
  onClose: () => void;
}) {
  const wins = settings.energyWindows ?? [];
  const [showDefault, setShowDefault] = useState(false);
  const num = (v: string, fallback: number) => {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : fallback;
  };
  const setWin = (i: number, patch: Partial<EnergyWindow>) =>
    onChange({
      energyWindows: wins.map((w, idx) => (idx === i ? { ...w, ...patch } : w)),
    });
  const inputCls =
    "rounded border border-border bg-background px-1 py-0.5 text-right text-foreground focus:border-primary/50 focus:outline-none";
  // One shared section-heading style so the sheet reads as grouped sections
  // ("When you work", "Breaks & protected time", …) instead of a flat wall.
  const sectionCls =
    "mb-1.5 mt-3 border-t border-border pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/80";
  // Lunch is "on" only when a valid window is stored (end > start); we toggle it
  // off by storing 0–0 rather than nulling (the settings PUT is additive).
  const lunchOn =
    settings.lunchStartHour != null &&
    settings.lunchEndHour != null &&
    settings.lunchEndHour > settings.lunchStartHour;
  const usingDefaultPrompt = !(settings.planningPrompt ?? "").trim();

  return (
    <>
      {/* Mobile-only backdrop — dims the app and taps-to-close. Hidden on
          desktop where this renders as an anchored popover instead. */}
      <div
        className="fixed inset-0 z-[75] bg-black/40 sm:hidden"
        onClick={onClose}
        aria-hidden
      />
      {/* Bottom sheet on phones (fixed, above the z-50 nav bar, safe-area
          padded so nothing hides behind the home indicator / menu bar);
          anchored popover on ≥sm. */}
      <div
        className="fixed inset-x-0 bottom-0 z-[80] flex max-h-[88vh] flex-col rounded-t-2xl border border-border bg-card text-xs shadow-2xl sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:z-30 sm:mt-2 sm:max-h-[80vh] sm:w-80 sm:rounded-lg sm:shadow-xl"
      >
        {/* grab handle (mobile affordance) */}
        <div className="flex shrink-0 justify-center pt-2 sm:hidden">
          <span className="h-1 w-10 rounded-full bg-border" />
        </div>
        <div className="flex shrink-0 items-center justify-between px-3 pb-2 pt-2">
          <span className="font-semibold text-foreground">Calendar settings</span>
          <Button variant="text" size="icon-xs" radius="keep" layout="" type="button" onClick={onClose} aria-label="Close" className="rounded">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        {/* Scrollable body — everything below the header scrolls, with
            safe-area bottom padding so the last control clears the menu bar. */}
        <div
          className="min-h-0 flex-1 overflow-y-auto px-3"
          style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
        >
          {/* How the AI plans your day — the standing planning philosophy. */}
          <div className="mb-3 rounded-md border border-primary/20 bg-primary/[0.04] p-2">
            <label className="mb-1 block font-medium text-foreground">
              How should the AI plan your day?
            </label>
            <p className="mb-1.5 text-[10px] text-muted-foreground">
              A standing instruction the planner follows every time you{" "}
              <b className="text-foreground">Rebuild my day</b> or{" "}
              <b className="text-foreground">Fit what&apos;s left</b>. Leave it
              blank to use the built-in default. (You can also add a one-off note
              for a single day right in the Rebuild my day panel.)
            </p>
            <textarea
              value={settings.planningPrompt ?? ""}
              onChange={(e) => onChange({ planningPrompt: e.target.value })}
              placeholder="Override the default — e.g. Front-load deep work in the morning, batch calls after lunch, keep Fridays light."
              rows={4}
              className="w-full resize-y rounded border border-border bg-background px-2 py-1.5 text-[11px] leading-snug text-foreground focus:border-primary/50 focus:outline-none"
            />
            <button
              type="button"
              onClick={() => setShowDefault((v) => !v)}
              className="mt-1.5 inline-flex items-center gap-0.5 text-[10px] font-medium text-primary hover:underline"
            >
              {showDefault ? (
                <Icon name="ChevronDown" className="h-3 w-3" />
              ) : (
                <Icon name="ChevronRight" className="h-3 w-3" />
              )}
              {usingDefaultPrompt
                ? "See the default the AI is using"
                : "See the default (your text overrides it)"}
            </button>
            {showDefault && (
              <p className="mt-1 rounded border border-border bg-background/60 p-2 text-[10px] italic leading-snug text-muted-foreground">
                {DEFAULT_PLANNING_PROMPT}
              </p>
            )}
          </div>

          <p className={sectionCls}>When you work</p>
          <label className="mb-1 flex items-center justify-between gap-2">
            <span className="text-muted-foreground">Working hours</span>
            <span className="flex items-center gap-1">
              <HourSelect
                value={settings.dayStartHour}
                min={0}
                max={23}
                fallback={7}
                onChange={(h) => onChange({ dayStartHour: h })}
              />
              <span className="text-muted-foreground">–</span>
              <HourSelect
                value={settings.dayEndHour}
                min={1}
                max={24}
                fallback={22}
                onChange={(h) => onChange({ dayEndHour: h })}
              />
            </span>
          </label>
          <p className="mb-2 text-[10px] text-muted-foreground">
            The grid always shows all 24 hours — these just shade your off-hours
            and set where the AI planner places work by default. You can still
            schedule any time.
          </p>

          <label className="mb-2 flex items-center justify-between gap-2">
            <span className="text-muted-foreground">Daily focus capacity (h)</span>
            <input
              type="number"
              min={0}
              max={16}
              step={0.5}
              value={Math.round((settings.dailyCapacityMins / 60) * 10) / 10}
              onChange={(e) =>
                onChange({
                  dailyCapacityMins: Math.round(num(e.target.value, 6) * 60),
                })
              }
              className={`w-14 ${inputCls}`}
            />
          </label>

          <label className="mb-2 flex items-center justify-between gap-2">
            <span className="text-muted-foreground">
              Buffer between blocks (min)
            </span>
            <input
              type="number"
              min={0}
              max={60}
              step={5}
              value={settings.bufferMins}
              onChange={(e) => onChange({ bufferMins: num(e.target.value, 0) })}
              className={`w-14 ${inputCls}`}
            />
          </label>

          {/* Breaks — so the day isn't wall-to-wall focus work. */}
          <p className={sectionCls}>Breaks &amp; protected time</p>
          <label className="mb-2 flex items-center justify-between gap-2">
            <span className="min-w-0 text-muted-foreground">
              Break after focus run (min, 0 = off)
            </span>
            <input
              type="number"
              min={0}
              max={240}
              step={15}
              value={settings.maxFocusRunMins}
              onChange={(e) =>
                onChange({ maxFocusRunMins: num(e.target.value, 90) })
              }
              className={`w-14 ${inputCls}`}
            />
          </label>
          <label className="mb-2 flex items-center justify-between gap-2">
            <span className="text-muted-foreground">Break length (min)</span>
            <input
              type="number"
              min={5}
              max={60}
              step={5}
              value={settings.breakMins}
              onChange={(e) => onChange({ breakMins: num(e.target.value, 10) })}
              className={`w-14 ${inputCls}`}
            />
          </label>

          {/* Protected lunch — the planner won't book over it. */}
          <label className="mb-2 flex cursor-pointer items-center justify-between gap-2">
            <span className="min-w-0 text-muted-foreground">
              Protect a lunch break
            </span>
            <input
              type="checkbox"
              checked={lunchOn}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? { lunchStartHour: 13, lunchEndHour: 14 }
                    : { lunchStartHour: 0, lunchEndHour: 0 },
                )
              }
              className="h-4 w-4 shrink-0 accent-primary"
            />
          </label>
          {lunchOn && (
            <label className="mb-3 flex items-center justify-between gap-2 pl-2">
              <span className="text-muted-foreground">Lunch window</span>
              <span className="flex items-center gap-1">
                <HourSelect
                  value={settings.lunchStartHour ?? 13}
                  min={0}
                  max={23}
                  fallback={13}
                  onChange={(h) => onChange({ lunchStartHour: h })}
                />
                <span className="text-muted-foreground">–</span>
                <HourSelect
                  value={settings.lunchEndHour ?? 14}
                  min={1}
                  max={24}
                  fallback={14}
                  onChange={(h) => onChange({ lunchEndHour: h })}
                />
              </span>
            </label>
          )}
          <p className="mb-2 text-[10px] text-muted-foreground">
            Lunch is the quick preset. For other daily protected time (gym,
            school run, family), add a Recurring&nbsp;→&nbsp;
            <b className="text-foreground">Block</b> below — same idea, on the
            days you choose.
          </p>

          <p className={sectionCls}>Automation</p>
          <label className="mb-1 flex cursor-pointer items-center justify-between gap-2">
            <span className="min-w-0 text-muted-foreground">
              Auto-return unfinished tasks to my list daily
            </span>
            <input
              type="checkbox"
              checked={settings.autoRollover}
              onChange={(e) => onChange({ autoRollover: e.target.checked })}
              className="h-4 w-4 shrink-0 accent-primary"
            />
          </label>
          <p className="mb-2 text-[10px] text-muted-foreground">
            Each morning, any unfinished tasks left on past days have their times
            cleared and move back to your unscheduled list, so you re-plan them
            (drag, or Rebuild my day) instead of them auto-filling the day.
          </p>

          <p className={sectionCls}>Energy &amp; themed time</p>
          <div className="mb-1 flex items-center justify-between">
            <span className="font-medium text-foreground">Energy windows</span>
            <button
              type="button"
              onClick={() =>
                onChange({
                  energyWindows: [
                    ...wins,
                    { start_hour: 9, end_hour: 12, energy: "high" },
                  ],
                })
              }
              className="inline-flex items-center gap-0.5 text-primary hover:underline"
            >
              <Icon name="Plus" className="h-3 w-3" /> Add
            </button>
          </div>
          <p className="mb-1.5 text-[10px] text-muted-foreground">
            When you&apos;re sharp vs tired: the planner puts high-energy work in
            peak windows, admin in low ones. (To reserve a time for a <i>kind</i>{" "}
            of work — calls, deep work — use a Recurring&nbsp;→&nbsp;Focus window
            below instead.)
          </p>
          {wins.length === 0 ? (
            <p className="text-[11px] text-muted-foreground/70">None set.</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {wins.map((w, i) => (
                <div key={i} className="flex items-center gap-1">
                  <HourSelect
                    value={w.start_hour}
                    min={0}
                    max={23}
                    fallback={9}
                    onChange={(h) => setWin(i, { start_hour: h })}
                  />
                  <span className="text-muted-foreground">–</span>
                  <HourSelect
                    value={w.end_hour}
                    min={1}
                    max={24}
                    fallback={12}
                    onChange={(h) => setWin(i, { end_hour: h })}
                  />
                  <select
                    value={w.energy}
                    onChange={(e) =>
                      setWin(i, {
                        energy: e.target.value as EnergyWindow["energy"],
                      })
                    }
                    className="min-w-0 flex-1 rounded border border-border bg-background px-1 py-1 text-foreground"
                  >
                    <option value="high">high</option>
                    <option value="medium">medium</option>
                    <option value="low">low</option>
                  </select>
                  <button
                    type="button"
                    onClick={() =>
                      onChange({
                        energyWindows: wins.filter((_, idx) => idx !== i),
                      })
                    }
                    aria-label="Remove window"
                    className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                  >
                    <Icon name="Trash2" className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Recurring windows — block out habits, reserve times for a kind of
              work. Flexible: add/edit/remove freely. */}
          {(() => {
            const tpls = settings.dayTemplates ?? [];
            const setTpls = (next: DayTemplate[]) =>
              onChange({ dayTemplates: next });
            const patch = (i: number, p: Partial<DayTemplate>) =>
              setTpls(tpls.map((t, idx) => (idx === i ? { ...t, ...p } : t)));
            const toggleDay = (i: number, d: number) => {
              const cur = tpls[i].days ?? [];
              patch(i, {
                days: cur.includes(d)
                  ? cur.filter((x) => x !== d)
                  : [...cur, d].sort((a, b) => a - b),
              });
            };
            return (
              <div className="mt-3 border-t border-border pt-2">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-medium text-foreground">
                    Recurring windows
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setTpls([
                        ...tpls,
                        {
                          days: [],
                          start_hour: 12,
                          end_hour: 13,
                          kind: "block",
                          label: "",
                          theme: "",
                        },
                      ])
                    }
                    className="inline-flex items-center gap-0.5 text-primary hover:underline"
                  >
                    <Icon name="Plus" className="h-3 w-3" /> Add
                  </button>
                </div>
                <p className="mb-1.5 text-[10px] text-muted-foreground">
                  <b className="text-foreground">Block</b> = protected time, no
                  tasks (lunch, gym, family).{" "}
                  <b className="text-foreground">Focus</b> = reserve for a kind of
                  work (deep work, calls, meetings).
                </p>
                {tpls.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground/70">None set.</p>
                ) : (
                  <div className="flex flex-col gap-2">
                    {tpls.map((t, i) => (
                      <div
                        key={i}
                        className="rounded-md border border-border bg-background/50 p-2"
                      >
                        <div className="flex items-center gap-1.5">
                          <select
                            value={t.kind}
                            onChange={(e) =>
                              patch(i, {
                                kind: e.target.value as DayTemplate["kind"],
                              })
                            }
                            className="rounded border border-border bg-background px-1 py-1 text-foreground"
                          >
                            <option value="block">Block</option>
                            <option value="focus">Focus</option>
                          </select>
                          <input
                            value={t.label}
                            onChange={(e) => patch(i, { label: e.target.value })}
                            placeholder={
                              t.kind === "block" ? "Lunch, Gym…" : "Deep work…"
                            }
                            className="min-w-0 flex-1 rounded border border-border bg-background px-1.5 py-1 text-foreground focus:border-primary/50 focus:outline-none"
                          />
                          <button
                            type="button"
                            onClick={() =>
                              setTpls(tpls.filter((_, idx) => idx !== i))
                            }
                            aria-label="Remove window"
                            className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                          >
                            <Icon name="Trash2" className="h-3 w-3" />
                          </button>
                        </div>
                        <div className="mt-1.5 flex items-center gap-1">
                          <HourSelect
                            value={t.start_hour}
                            min={0}
                            max={23}
                            fallback={9}
                            onChange={(h) => patch(i, { start_hour: h })}
                          />
                          <span className="text-muted-foreground">–</span>
                          <HourSelect
                            value={t.end_hour}
                            min={1}
                            max={24}
                            fallback={10}
                            onChange={(h) => patch(i, { end_hour: h })}
                          />
                          {t.kind === "focus" && (
                            <input
                              value={t.theme}
                              onChange={(e) =>
                                patch(i, { theme: e.target.value })
                              }
                              placeholder="theme: calls, deep…"
                              className="min-w-0 flex-1 rounded border border-border bg-background px-1.5 py-1 text-[11px] text-foreground focus:border-primary/50 focus:outline-none"
                            />
                          )}
                        </div>
                        <div className="mt-1.5 flex items-center gap-1">
                          {DOW_LABELS.map((d, di) => {
                            const on = (t.days ?? []).includes(di);
                            return (
                              <button
                                key={di}
                                type="button"
                                onClick={() => toggleDay(i, di)}
                                className={[
                                  "h-6 w-6 rounded text-[10px] font-medium",
                                  on
                                    ? "bg-primary text-primary-foreground"
                                    : "bg-secondary text-muted-foreground hover:text-foreground",
                                ].join(" ")}
                              >
                                {d}
                              </button>
                            );
                          })}
                          <span className="ml-1 text-[10px] text-muted-foreground/70">
                            {(t.days ?? []).length === 0 ? "every day" : ""}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>
    </>
  );
}
