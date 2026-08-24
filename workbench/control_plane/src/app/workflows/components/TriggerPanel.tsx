"use client";

/**
 * Trigger bindings panel (RFC §7) — manual is always available; webhook
 * exposes the per-workflow hook URL (+ optional HMAC secret); schedule takes
 * a cron expression. All kinds converge on the same run entrypoint.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useMemo, useState } from "react";
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
}: {
  triggers: TriggerSpec[];
  /** Absolute gateway URL from the server; "" when the origin is unset. */
  hookUrl?: string;
  hookPath?: string;
  published: boolean;
  onChange: (next: TriggerSpec[]) => void;
  onClose: () => void;
}) {
  const webhook = useMemo(
    () => triggers.find((t) => t.kind === "webhook"),
    [triggers],
  );
  const schedule = useMemo(
    () => triggers.find((t) => t.kind === "schedule"),
    [triggers],
  );
  const event = useMemo(
    () => triggers.find((t) => t.kind === "event"),
    [triggers],
  );
  const [copied, setCopied] = useState(false);

  // The gateway names its own public URL (PUBLIC_API_BASE_URL). We do NOT
  // assemble one from window.location: that origin is the control plane, and
  // its /api proxy re-serializes JSON — which silently breaks any HMAC the
  // sender computed. When the origin is unconfigured we show the path and say
  // so, rather than handing out a URL that 404s in production.
  const hookUrl = gatewayHookUrl ?? "";
  const hookOriginMissing = !hookUrl && Boolean(hookPath);

  const upsert = (kind: "webhook" | "schedule" | "event", patch: Partial<TriggerSpec>) => {
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
    <div className="absolute right-3 top-12 z-20 w-80 rounded-xl border border-border bg-popover shadow-lg">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
          <Icon name="Zap" className="w-3.5 h-3.5 text-amber-500" />
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
            <span className="font-medium text-foreground">Platform event</span>
            <input
              type="checkbox"
              checked={Boolean(event?.enabled)}
              onChange={(e) =>
                e.target.checked
                  ? upsert("event", {
                      enabled: true,
                      config: { source: String(event?.config.source ?? "zoho") },
                    })
                  : remove("event")
              }
            />
          </div>
          {event?.enabled && (
            <div className="mt-1.5 space-y-1">
              <div className="flex gap-1.5">
                <select
                  value={String(event.config.source ?? "zoho")}
                  onChange={(e) =>
                    upsert("event", {
                      config: { ...event.config, source: e.target.value },
                    })
                  }
                  className={inputCls}
                >
                  <option value="zoho">zoho</option>
                  <option value="gmail">gmail</option>
                  <option value="custom">custom</option>
                </select>
                <input
                  placeholder="event type (blank = all)"
                  value={String(event.config.event_type ?? "")}
                  onChange={(e) =>
                    upsert("event", {
                      config: { ...event.config, event_type: e.target.value },
                    })
                  }
                  className={inputCls}
                />
              </div>
              <p className="text-[10px] text-muted-foreground">
                Fires on provider events (e.g. Zoho{" "}
                <code className="bg-secondary px-1 rounded">Contacts.edit</code>)
                and on signed posts to{" "}
                <code className="bg-secondary px-1 rounded">
                  /agent/webhook/&#123;source&#125;
                </code>
                . The event payload arrives as <code>{"{{trigger.*}}"}</code>.
              </p>
              {!published && (
                <p className="text-[10px] text-warning">
                  Fires only once the workflow is published.
                </p>
              )}
            </div>
          )}
        </div>

        <p className="text-[10px] text-muted-foreground border-t border-border pt-2">
          Trigger changes save with the workflow.
        </p>
      </div>
    </div>
  );
}
