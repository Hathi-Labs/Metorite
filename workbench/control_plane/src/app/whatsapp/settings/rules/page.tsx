"use client";

// WhatsApp Rules — a dry-run of the auto-reply engine over the current queue.
// The "honest stats" ethos: the founder sees exactly what automation WOULD do
// (no sends) before enabling any of it. Each row shows the decided action and
// why; the summary tallies actions by kind.

import Icon from "@/components/Icon";
import { useEffect, useState } from "react";
import { fetchAccounts, fetchRulesPreview, pickDefaultAccount } from "../../lib/api";
import type { WaRulePreview, WaRulePreviewItem } from "../../lib/types";

const ACTION_LABEL: Record<string, string> = {
  answer_from_system: "Auto-answer",
  holding_reply: "Holding reply",
  draft: "Prepare draft",
  none: "Leave for you",
};

const ACTION_TONE: Record<string, string> = {
  answer_from_system: "text-success",
  holding_reply: "text-success",
  draft: "text-primary",
  none: "text-muted-foreground",
};

export default function RulesPreviewPage() {
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<WaRulePreview>({
    items: [],
    summary: {},
  });

  useEffect(() => {
    (async () => {
      const def = pickDefaultAccount(await fetchAccounts());
      if (def) setPreview(await fetchRulesPreview(def.id));
      setLoading(false);
    })();
  }, []);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Icon name="Loader2" className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  const order = ["answer_from_system", "holding_reply", "draft", "none"];
  const summaryEntries = order
    .filter((k) => preview.summary[k])
    .map((k) => [k, preview.summary[k]] as const);

  return (
    <div className="mx-auto h-full max-w-3xl overflow-y-auto p-4 text-foreground md:p-6">
      <div className="mb-5 flex items-center gap-2">
        <Icon name="SlidersHorizontal" className="h-4 w-4 text-primary" />
        <h1 className="text-[15px] font-semibold">Rules preview</h1>
        <span className="text-[11px] text-muted-foreground">
          what automation would do — nothing is sent
        </span>
      </div>

      {/* summary tiles */}
      {summaryEntries.length > 0 && (
        <div className="mb-5 flex flex-wrap gap-2">
          {summaryEntries.map(([action, n]) => (
            <div
              key={action}
              className="rounded-lg border border-border px-3 py-2"
            >
              <span className={`text-[16px] font-bold ${ACTION_TONE[action]}`}>
                {n}
              </span>
              <span className="ml-1.5 text-[11px] text-muted-foreground">
                {ACTION_LABEL[action] ?? action}
              </span>
            </div>
          ))}
        </div>
      )}

      {preview.items.length === 0 ? (
        <div className="rounded-lg border border-border p-6 text-center text-[13px] text-muted-foreground">
          Nothing in the needs-reply queue to preview right now.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[520px] text-[12px]">
            <thead>
              <tr className="border-b border-border text-[9.5px] uppercase tracking-wider text-muted-foreground/70">
                <th className="px-3 py-2 text-left font-bold">Chat</th>
                <th className="px-3 py-2 text-left font-bold">Intent</th>
                <th className="px-3 py-2 text-left font-bold">Would do</th>
                <th className="px-3 py-2 text-left font-bold">Why</th>
              </tr>
            </thead>
            <tbody>
              {preview.items.map((it) => (
                <RuleRow key={it.chat_id} it={it} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-4 text-[11px] text-muted-foreground/70">
        Auto-answers run unattended; holding replies are canned and safe; drafts
        wait for your Send. VIP and Family are never auto-sent.
      </p>
    </div>
  );
}

function RuleRow({ it }: { it: WaRulePreviewItem }) {
  return (
    <tr className="border-b border-border last:border-0">
      <td className="px-3 py-2.5">
        <span className="font-semibold">{it.name}</span>
        {it.category && (
          <span className="ml-1.5 text-[10px] text-muted-foreground">
            {it.category}
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 text-muted-foreground">{it.intent ?? "—"}</td>
      <td className="px-3 py-2.5">
        <span className={`font-semibold ${ACTION_TONE[it.action] ?? ""}`}>
          {ACTION_LABEL[it.action] ?? it.action}
        </span>
        {it.via_template && (
          <span className="ml-1 text-[10px] text-warning">· template</span>
        )}
        {it.requires_approval && (
          <span className="ml-1 text-[10px] text-muted-foreground">
            · approve
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 text-[11px] text-muted-foreground">
        {it.reason}
      </td>
    </tr>
  );
}
