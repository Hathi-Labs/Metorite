/**
 * WS-55 SW-24: the handoff note of the last agent, as a card above the
 * comments. It only reads. The text is what an agent wrote, so it shows as
 * plain text and never as markup.
 */

import Icon from "@/components/Icon";

import { HANDOFF_FIELDS, handoffByline, handoffOf } from "../lib/agentHandoff";
import type { AgentActivity } from "../lib/agentRuns";

interface Props {
  activity: AgentActivity;
  labels?: ReadonlyMap<string, string>;
}

export function HandoffCard({ activity, labels }: Props) {
  const note = handoffOf(activity);
  if (!note) return null;
  return (
    <section
      aria-label="Handoff note"
      data-agent-handoff=""
      className="mb-3 space-y-1.5 rounded-lg border border-border bg-card p-3 text-xs"
    >
      <p className="flex min-w-0 items-center gap-1.5 font-medium text-foreground">
        <Icon name="LogOut" size={12} className="shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 truncate" data-agent-handoff-byline="">
          {handoffByline(activity, labels)}
        </span>
      </p>
      <dl className="space-y-1">
        {HANDOFF_FIELDS.map(({ key, label }) =>
          note[key] ? (
            <div key={key}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="whitespace-pre-wrap break-words text-foreground">{note[key]}</dd>
            </div>
          ) : null,
        )}
      </dl>
    </section>
  );
}
