"use client";

/**
 * "Status and stage" — the one help text for the two words (WS-41 I-10b,
 * `project_import.md` §7.7).
 *
 * The import's Map step and the Statuses screen of Settings both show it, so
 * a member reads the same words in both places. It reads `STATUS_AND_STAGE`
 * and `CATEGORY_HINT`, and keeps no copy: `stageHelp.test.ts` fails on a
 * literal copy anywhere under `src/app/projects/`.
 */
import InfoTip from "@/components/ui/InfoTip";
import { statusAccent } from "@/lib/statusAccent";
import {
  CATEGORY_HINT,
  CATEGORY_LABEL,
  EDITABLE_CATEGORIES,
  STATUS_AND_STAGE,
} from "@/lib/statusCategory";

export const STATUS_AND_STAGE_TITLE = "Status and stage";

/** What each word means, then the five stages with their meanings. */
export function StatusStageHelp() {
  return (
    <>
      <p>{STATUS_AND_STAGE}</p>
      <dl className="space-y-1.5 pt-1">
        {EDITABLE_CATEGORIES.map((stage) => (
          <div key={stage}>
            <dt className="flex items-center gap-1.5 font-medium">
              <span className={`h-2 w-2 shrink-0 rounded-full ${statusAccent({ category: stage }).dot}`} />
              {CATEGORY_LABEL[stage]}
            </dt>
            <dd className="pl-3.5 text-muted-foreground">{CATEGORY_HINT[stage]}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

/** The "i" beside a Statuses heading. */
export function StatusStageTip() {
  return (
    <InfoTip label={STATUS_AND_STAGE_TITLE} title={STATUS_AND_STAGE_TITLE}>
      <StatusStageHelp />
    </InfoTip>
  );
}

/**
 * The "i" beside one stage's heading: that stage's meaning. The caller hands
 * over the text it already reads from `CATEGORY_HINT` (`groupByCategory`).
 */
export function StageTip({ label, hint }: { label: string; hint: string }) {
  return (
    <InfoTip label={`What ${label} means`} title={label}>
      <p className="text-muted-foreground">{hint}</p>
    </InfoTip>
  );
}
