"use client";

/**
 * CrmEvidence — a CRM read, drawn inside its step in the working trail.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24 rule 2, and §24.8
 * (follow-up of #716 and #735). The CRM reads (`agent-crm/agents.py`) had no
 * card file, so the step opened to the raw output: `• Ravi @ Acme ·
 * ravi@acme.io (id=…)` and `• status_id: <uuid>`. They now draw through the
 * one `Readout`: no id, a labelled fact for each `key: value`, a chip for a
 * stage.
 *
 * The CRM writes ask the member first and keep their step row. This file
 * draws no write.
 */

import Readout from "@/components/Readout";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { placementOf } from "@/lib/chatPlacement";
import { bareToolName } from "@/lib/toolSteps";

/** Every tool this file draws. The placement fence reads it. */
export const CRM_CARD_TOOLS: readonly string[] = ["search_crm", "get_pipeline", "get_record", "get_timeline"];
const CRM_CARD_SET: ReadonlySet<string> = new Set(CRM_CARD_TOOLS);

/** The receipt of a CRM read for the trail, or null for any other tool. */
export function crmEvidence(e: ToolEvent): React.ReactNode | null {
  if (e.status !== "done") return null;
  const name = bareToolName(e.name);
  if (!CRM_CARD_SET.has(name) || placementOf(name) !== "evidence") return null;
  return (
    <div data-crm-evidence="" className="min-w-0 rounded-md border border-border/60 bg-card/60 px-2.5 py-2">
      <div className="max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin">
        <Readout result={e.result || ""} />
      </div>
    </div>
  );
}
