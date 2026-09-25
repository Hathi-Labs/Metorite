"use client";

import { StatusMenu } from "@/components/ui/StatusMenu";
import { CATEGORY_LABEL, landingLane } from "@/lib/statusCategory";
import { useEffect, useMemo, useState } from "react";

import type { PanelAnchor } from "@/components/ui/AnchoredPanel";
import { useTaskStore } from "../lib/taskStore";

/**
 * D79 — the drag prompt. A card dropped on a stage that holds two or more
 * statuses lands there with NO write, and this asks which status it means.
 *
 * The menu hangs from the card in its new column, lists only that stage, and
 * opens on the stage's FIRST status, so Enter confirms the answer a drag used
 * to write silently. Escape or a click outside puts the card back and writes
 * nothing (`cancelStagePrompt`).
 *
 * Mounted once per page, beside `UndoToast`. The store holds the question
 * (`stagePrompt`), so the board and the list share one host.
 */
export function StagePromptHost() {
  const prompt = useTaskStore((s) => s.stagePrompt);
  const confirm = useTaskStore((s) => s.confirmStagePrompt);
  const cancel = useTaskStore((s) => s.cancelStagePrompt);

  // The card re-renders in its new column after the prompt opens, so the
  // anchor is looked up one frame later rather than at the drop.
  const [anchor, setAnchor] = useState<PanelAnchor | null>(null);
  useEffect(() => {
    if (!prompt) {
      setAnchor(null);
      return;
    }
    const frame = requestAnimationFrame(() => setAnchor(prompt.anchor?.() ?? null));
    return () => cancelAnimationFrame(frame);
  }, [prompt]);

  const first = useMemo(
    () => (prompt ? landingLane(prompt.lanes, prompt.stage)?.id ?? null : null),
    [prompt],
  );

  if (!prompt) return null;
  const stage = CATEGORY_LABEL[prompt.stage] ?? prompt.stage;
  return (
    <StatusMenu
      anchor={anchor}
      open={anchor !== null}
      projectName={prompt.projectName}
      prompt={`Which ${stage} status? Enter picks the first.`}
      statuses={prompt.lanes}
      onlyCategory={prompt.stage}
      focusId={first}
      onPick={confirm}
      onClose={cancel}
    />
  );
}
