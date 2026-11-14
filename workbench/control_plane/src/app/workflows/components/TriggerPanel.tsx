"use client";

/**
 * Trigger bindings panel (RFC §7) — manual is always available; webhook
 * exposes the per-workflow hook URL (+ optional HMAC secret); schedule takes
 * a cron expression. All kinds converge on the same run entrypoint.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import Checkbox from "@/components/ui/Checkbox";
import Input from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { useMemo, useState } from "react";
import {
  EVENT_SOURCES,
  KNOWN_EVENT_SOURCES,
  STUDIO_EVENT_TYPES,
  STUDIO_OPS,
  addEvent,
  eventIndexes,
  filterOf,
  isStudio,
  patchAt,
  removeAt,
  setEventType,
  setFilterKey,
  setSource,
  sourceOf,
} from "../lib/eventTriggers";
import type { TriggerSpec } from "../lib/types";

const inputCls =
  "w-full rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring";

/** Suggestions only — the field accepts any IANA zone the server can resolve. */
const COMMON_TIMEZONES = [
  "UTC",
  "Asia/Kolkata",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
  "Asia/Singapore",
  "Asia/Dubai",
  "Australia/Sydney",
];

export default function TriggerPanel({
  triggers,
  hookUrl: gatewayHookUrl,
  hookPath,
  published,
  onChange,
  onClose,
  error,
}: {
  triggers: TriggerSpec[];
  /** Absolute gateway URL from the server; "" when the origin is unset. */
  hookUrl?: string;
  hookPath?: string;
  published: boolean;
  onChange: (next: TriggerSpec[]) => void;
  onClose: () => void;
  /** The message of a refused save, shown in full. */
  error?: string | null;
}) {
  const webhook = useMemo(
    () => triggers.find((t) => t.kind === "webhook"),
    [triggers],
  );
  const schedule = useMemo(
    () => triggers.find((t) => t.kind === "schedule"),
    [triggers],
  );
  const eventRows = useMemo(() => eventIndexes(triggers), [triggers]);
  const [copied, setCopied] = useState(false);

  // The gateway names its own public URL (PUBLIC_API_BASE_URL). We do NOT
  // assemble one from window.location: that origin is the control plane, and
  // its /api proxy re-serializes JSON — which silently breaks any HMAC the
  // sender computed. When the origin is unconfigured we show the path and say
  // so, rather than handing out a URL that 404s in production.
  const hookUrl = gatewayHookUrl ?? "";
  const hookOriginMissing = !hookUrl && Boolean(hookPath);

  const upsert = (kind: "webhook" | "schedule", patch: Partial<TriggerSpec>) => {
    const rest = triggers.filter((t) => t.kind !== kind);
    const current = triggers.find((t) => t.kind === kind) ?? {
      kind,
      config: {},
      enabled: false,
    };
    onChange([...rest, { ...current, ...patch } as TriggerSpec]);
  };

  const remove = (kind: string) =>
    onChange(triggers.filter((t) => t.kind !== kind));

  return (
    <div className="absolute right-3 top-12 z-20 w-80 rounded-xl border border-border bg-popover shadow-lg max-h-[80vh] overflow-y-auto">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
          <Icon name="Zap" className="w-3.5 h-3.5 text-cat-12" />
          Triggers
        </span>
        <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={onClose} className="rounded-md">
          <Icon name="X" className="w-3.5 h-3.5" />
        </Button>
      </div>

      <div className="p-3 space-y-4 text-xs">
        <div>
          <div className="font-medium text-foreground">Manual / API</div>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            Always available — the Run button here, or{" "}
            <code className="bg-secondary px-1 rounded">
              POST /workflows/&#123;id&#125;/run
            </code>
            .
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <span className="font-medium text-foreground">Webhook</span>
            <input
              type="checkbox"
              checked={Boolean(webhook?.enabled)}
              onChange={(e) =>
                e.target.checked
                  ? upsert("webhook", { enabled: true })
                  : remove("webhook")
              }
            />
          </div>
          {webhook?.enabled && (
            <div className="mt-1.5 space-y-1.5">
              <div className="flex items-center gap-1">
                <input
                  readOnly
                  value={hookUrl || hookPath || ""}
                  className={`${inputCls} font-mono text-[10px]`}
                />
                <button
                  onClick={() => {
                    navigator.clipboard?.writeText(hookUrl || hookPath || "");
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  }}
                  disabled={!hookUrl && !hookPath}
                  className="p-1.5 rounded-lg border border-border text-muted-foreground hover:text-foreground tech-transition shrink-0 disabled:opacity-50"
                  title="Copy URL"
                >
                  <Icon name="Copy" className="w-3.5 h-3.5" />
                </button>
              </div>
              {copied && (
                <p className="text-[10px] text-success">Copied.</p>
              )}
              {hookOriginMissing && (
                <p className="text-[10px] text-warning">
                  Path only — set{" "}
                  <code className="bg-secondary px-1 rounded">
                    PUBLIC_API_BASE_URL
                  </code>{" "}
                  on the gateway to get a full URL. Point senders at the
                  gateway host, not this one.
                </p>
              )}
              <input
                placeholder="Optional HMAC secret (X-CC-Signature)"
                value={String(webhook.config.secret ?? "")}
                onChange={(e) =>
                  upsert("webhook", {
                    config: { ...webhook.config, secret: e.target.value },
                  })
                }
                className={inputCls}
              />
              {!published && (
                <p className="text-[10px] text-warning">
                  Fires only once the workflow is published.
                </p>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="flex items-center justify-between">
            <span className="font-medium text-foreground">Schedule (cron)</span>
            <input
              type="checkbox"
              checked={Boolean(schedule?.enabled)}
              onChange={(e) =>
                e.target.checked
                  ? upsert("schedule", {
                      enabled: true,
                      config: { cron: String(schedule?.config.cron ?? "0 9 * * *") },
                    })
                  : remove("schedule")
              }
            />
          </div>
          {schedule?.enabled && (
            <div className="mt-1.5 space-y-1">
              <input
                placeholder="0 9 * * 1-5"
                value={String(schedule.config.cron ?? "")}
                onChange={(e) =>
                  upsert("schedule", {
                    config: { ...schedule.config, cron: e.target.value },
                  })
                }
                className={`${inputCls} font-mono`}
              />
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-muted-foreground shrink-0">
                  in
                </span>
                <input
                  list="wf-timezones"
                  placeholder="UTC"
                  value={String(schedule.config.timezone ?? "")}
                  onChange={(e) =>
                    upsert("schedule", {
                      config: { ...schedule.config, timezone: e.target.value },
                    })
                  }
                  className={`${inputCls} font-mono`}
                />
                <datalist id="wf-timezones">
                  {COMMON_TIMEZONES.map((tz) => (
                    <option key={tz} value={tz} />
                  ))}
                </datalist>
              </div>
              <p className="text-[10px] text-muted-foreground">
                Five-field cron. e.g.{" "}
                <code className="bg-secondary px-1 rounded">0 9 * * 1-5</code> =
                weekdays 09:00 in the timezone above (blank = UTC). The zone is
                a wall clock, so a 9am schedule stays 9am across daylight-saving
                changes.
              </p>
              {!published && (
                <p className="text-[10px] text-warning">
                  Fires only once the workflow is published.
                </p>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="flex items-center justify-between">
            <span className="font-medium text-foreground">Events</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onChange(addEvent(triggers))}
            >
              <Icon name="Plus" className="w-3.5 h-3.5" />
              Add event
            </Button>
          </div>
          {eventRows.length === 0 && (
            <p className="text-[10px] text-muted-foreground mt-0.5">
              No event starts this workflow. Add one to start it on a
              provider event or a Studio record change.
            </p>
          )}
          {eventRows.map((index, n) => {
            const row = triggers[index];
            const source = sourceOf(row);
            const studio = isStudio(row);
            const filter = filterOf(row);
            return (
              <div
                key={row.id ?? `event-${index}`}
                data-testid="event-trigger"
                className="mt-1.5 space-y-1 rounded-lg border border-border p-2"
              >
                <div className="flex items-center gap-1.5">
                  <Checkbox
                    checked={Boolean(row.enabled)}
                    onChange={(e) =>
                      onChange(patchAt(triggers, index, { enabled: e.target.checked }))
                    }
                    aria-label={`Event ${n + 1} is on`}
                  />
                  <span className="text-[10px] text-muted-foreground">
                    Event {n + 1}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    radius="keep"
                    layout=""
                    onClick={() => onChange(removeAt(triggers, index))}
                    className="ml-auto rounded-md"
                    aria-label={`Remove event ${n + 1}`}
                  >
                    <Icon name="X" className="w-3.5 h-3.5" />
                  </Button>
                </div>
                {/* A stored source no longer on the list — `clickup` after D52 —
                    must still RENDER. Without its own option the control shows
                    BLANK, so an existing workflow looked unconfigured, and the
                    first save silently rewrote its trigger. It is named as
                    retired until somebody re-points it. */}
                <SelectButton
                  label="Event source"
                  value={source}
                  defaultValue="zoho"
                  options={
                    KNOWN_EVENT_SOURCES.includes(source)
                      ? EVENT_SOURCES
                      : [...EVENT_SOURCES, { value: source, label: `(retired) ${source}` }]
                  }
                  onChange={(v) => onChange(setSource(triggers, index, v))}
                />
                {studio ? (
                  <>
                    <SelectButton
                      label="Studio event"
                      value={String(row.config.event_type ?? "")}
                      defaultValue=""
                      options={[{ value: "", label: "Any record event" }, ...STUDIO_EVENT_TYPES]}
                      onChange={(v) => onChange(setEventType(triggers, index, v))}
                    />
                    <Input
                      inputSize="sm"
                      placeholder="App (blank = any app)"
                      aria-label="Studio app"
                      value={filter.package_id}
                      onChange={(e) =>
                        onChange(setFilterKey(triggers, index, "package_id", e.target.value))
                      }
                    />
                    <Input
                      inputSize="sm"
                      placeholder="Object (blank = any object)"
                      aria-label="Studio object"
                      value={filter.object}
                      onChange={(e) =>
                        onChange(setFilterKey(triggers, index, "object", e.target.value))
                      }
                    />
                    <SelectButton
                      label="Studio operation"
                      value={filter.op}
                      defaultValue=""
                      options={[{ value: "", label: "Any operation" }, ...STUDIO_OPS]}
                      onChange={(v) => onChange(setFilterKey(triggers, index, "op", v))}
                    />
                    <p className="text-[10px] text-muted-foreground">
                      Starts the workflow when a Studio record changes. Only
                      the author of this workflow can add or change a Studio
                      trigger. The record arrives as{" "}
                      <code>{"{{trigger.*}}"}</code>.
                    </p>
                  </>
                ) : (
                  <>
                    <Input
                      inputSize="sm"
                      placeholder="event type (blank = all)"
                      aria-label="Event type"
                      value={String(row.config.event_type ?? "")}
                      onChange={(e) =>
                        onChange(setEventType(triggers, index, e.target.value))
                      }
                    />
                    <p className="text-[10px] text-muted-foreground">
                      Fires on provider events (e.g. Zoho{" "}
                      <code className="bg-secondary px-1 rounded">Contacts.edit</code>)
                      and on signed posts to{" "}
                      <code className="bg-secondary px-1 rounded">
                        /agent/webhook/&#123;source&#125;
                      </code>
                      . The event payload arrives as <code>{"{{trigger.*}}"}</code>.
                    </p>
                  </>
                )}
                {!published && row.enabled && (
                  <p className="text-[10px] text-warning">
                    Fires only once the workflow is published.
                  </p>
                )}
              </div>
            );
          })}
        </div>

        {error && (
          <p role="alert" className="text-[10px] text-destructive">
            {error}
          </p>
        )}

        <p className="text-[10px] text-muted-foreground border-t border-border pt-2">
          Trigger changes save with the workflow.
        </p>
      </div>
    </div>
  );
}
