"use client";

/**
 * TriageRail — the front door's queue, above the board (WS-27u).
 *
 * Spec: `ai-company-brain/specs/project_management_app.md` §9.1 (WS-27u).
 *
 * Lists the selected project's pending captures (plus snoozes whose time has
 * come — the server decides that by reading `snoozed_until`, nothing here
 * polls) with the four rulings: accept → pick a lane, decline, mark duplicate
 * → task picker, snooze → a day. Every ruling is a POST to the intake
 * endpoints; the rail never edits a task directly, so the gateway's guards —
 * in-place flip, permanent wrapper, R5 — are the only implementation.
 *
 * Renders nothing when the queue is empty: a rail announcing an empty queue
 * on every project would be noise, and the badge on the collapsed bar is the
 * count that matters.
 */

import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";

import { intakeApi, projectsApi, type StatusRow } from "../lib/api";
import {
  acceptTargets,
  describeSource,
  duplicateTargets,
  snoozeInstant,
  sortQueue,
  type IntakeItem,
} from "../lib/intake";
import { isSearchableQuery, type Hit } from "../lib/search";

type Ruling = "accept" | "duplicate" | "snooze";

interface Props {
  projectId: string;
  /** The selected root's statuses — the accept picker's lanes. */
  statuses: StatusRow[];
  /** Open the capture in the task panel. */
  onOpenTask?: (taskId: string) => void;
  /** A ruling landed — the board may now be stale (an accept adds a card). */
  onResolved?: () => void;
}

export function TriageRail({ projectId, statuses, onOpenTask, onResolved }: Props) {
  const [items, setItems] = useState<IntakeItem[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which item has which picker open. One at a time, because two open pickers
  // is two half-made rulings.
  const [picking, setPicking] = useState<{ taskId: string; ruling: Ruling } | null>(
    null
  );
  const [duplicateQuery, setDuplicateQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [snoozeDay, setSnoozeDay] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await intakeApi.queue({ project_id: projectId });
      setItems(sortQueue(res.rows));
    } catch {
      // A board that works without its rail beats a board that refuses to
      // load because the queue did not.
      setItems([]);
    }
  }, [projectId]);

  useEffect(() => {
    setPicking(null);
    setError(null);
    void load();
  }, [load]);

  useEffect(() => {
    // The duplicate picker rides the existing search surface — same ranking,
    // same visibility, same escaping as ⌘K.
    const term = duplicateQuery.trim();
    if (!isSearchableQuery(term)) {
      setHits([]);
      return;
    }
    let live = true;
    void projectsApi
      .search(term)
      .then((res) => {
        if (live) setHits(res.rows);
      })
      .catch(() => {
        if (live) setHits([]);
      });
    return () => {
      live = false;
    };
  }, [duplicateQuery]);

  async function rule(taskId: string, action: () => Promise<unknown>) {
    setBusy(taskId);
    setError(null);
    try {
      await action();
      setPicking(null);
      setDuplicateQuery("");
      setSnoozeDay("");
      await load();
      onResolved?.();
    } catch (err) {
      // The gateway's own message — a 422 names the real refusal (a triage
      // destination, a past snooze) better than anything invented here.
      setError(String((err as Error).message));
    } finally {
      setBusy(null);
    }
  }

  function togglePicker(taskId: string, ruling: Ruling) {
    setError(null);
    setPicking((current) =>
      current?.taskId === taskId && current.ruling === ruling
        ? null
        : { taskId, ruling }
    );
  }

  if (items.length === 0) return null;

  return (
    <section className="border-b border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground hover:bg-muted"
        aria-expanded={open}
      >
        <Icon name="Inbox" size={16} className="text-primary" />
        <span className="font-medium">Triage</span>
        <Badge tone="primary">{items.length}</Badge>
        <span className="text-xs text-muted-foreground">
          captured, awaiting a ruling
        </span>
        <Icon
          name={open ? "ChevronUp" : "ChevronDown"}
          size={14}
          className="ml-auto text-muted-foreground"
        />
      </button>

      {open ? (
        <ul className="divide-y divide-border">
          {error ? (
            <li className="px-3 py-1.5 text-xs text-destructive">{error}</li>
          ) : null}
          {items.map((item) => {
            const picker = picking?.taskId === item.id ? picking.ruling : null;
            const from = describeSource(item.intake);
            const acting = busy === item.id;
            return (
              <li key={item.id} className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenTask?.(item.id)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <span className="block truncate text-sm text-foreground">
                      {item.title}
                    </span>
                    {from ? (
                      <span className="block truncate text-xs text-muted-foreground">
                        {from}
                      </span>
                    ) : null}
                  </button>
                  {item.intake.status === "snoozed" ? (
                    <Badge tone="warning" icon="AlarmClock">
                      snoozed
                    </Badge>
                  ) : null}
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="Check"
                      loading={acting}
                      onClick={() => togglePicker(item.id, "accept")}
                    >
                      Accept
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="X"
                      loading={acting}
                      onClick={() => void rule(item.id, () => intakeApi.decline(item.id))}
                    >
                      Decline
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="Copy"
                      loading={acting}
                      onClick={() => togglePicker(item.id, "duplicate")}
                    >
                      Duplicate
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="AlarmClock"
                      loading={acting}
                      onClick={() => togglePicker(item.id, "snooze")}
                    >
                      Snooze
                    </Button>
                  </div>
                </div>

                {picker === "accept" ? (
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    <span className="text-xs text-muted-foreground">Into:</span>
                    {acceptTargets(statuses).map((status) => (
                      <Button
                        key={status.id}
                        variant="secondary"
                        size="sm"
                        loading={acting}
                        onClick={() =>
                          void rule(item.id, () => intakeApi.accept(item.id, status.id))
                        }
                      >
                        {status.name}
                      </Button>
                    ))}
                  </div>
                ) : null}

                {picker === "duplicate" ? (
                  <div className="mt-2 space-y-1">
                    <Input
                      inputSize="sm"
                      icon="Search"
                      autoFocus
                      value={duplicateQuery}
                      onChange={(e) => setDuplicateQuery(e.target.value)}
                      placeholder="Find the task this duplicates…"
                      aria-label="Find the original task"
                    />
                    {duplicateTargets(hits, item.id).map((hit) => (
                      <button
                        key={hit.id}
                        type="button"
                        onClick={() =>
                          void rule(item.id, () => intakeApi.duplicate(item.id, hit.id))
                        }
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs text-foreground hover:bg-muted"
                      >
                        <span className="truncate">{hit.title}</span>
                        {hit.project_name ? (
                          <span className="ml-auto shrink-0 text-muted-foreground">
                            {hit.project_name}
                          </span>
                        ) : null}
                      </button>
                    ))}
                  </div>
                ) : null}

                {picker === "snooze" ? (
                  <div className="mt-2 flex items-center gap-2">
                    <Input
                      inputSize="sm"
                      type="date"
                      autoFocus
                      value={snoozeDay}
                      onChange={(e) => setSnoozeDay(e.target.value)}
                      aria-label="Snooze until"
                    />
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={acting}
                      disabled={!snoozeInstant(snoozeDay)}
                      onClick={() => {
                        const until = snoozeInstant(snoozeDay);
                        if (until) {
                          void rule(item.id, () => intakeApi.snooze(item.id, until));
                        }
                      }}
                    >
                      Snooze
                    </Button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
