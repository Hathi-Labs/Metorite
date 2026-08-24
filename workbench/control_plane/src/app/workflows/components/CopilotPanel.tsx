"use client";

/**
 * The Workflow Copilot panel — chat-to-build inside the editor (spec F14).
 * The maker describes what the workflow should do; the copilot (server-side,
 * shortlisting capabilities via the same semantic search as the palette)
 * returns an updated graph and any modules it had to create. The graph is
 * applied to the canvas immediately — with one-click Undo — and created
 * modules land in the org library with auto-created provenance.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useRef, useState } from "react";
import { askCopilot } from "../lib/api";
import type { CopilotResponse, WorkflowGraph } from "../lib/types";

type Turn = {
  role: "user" | "assistant";
  content: string;
  createdModules?: { id: string; name: string }[];
  applied?: boolean;
  problems?: string[];
};

export default function CopilotPanel({
  workflowId,
  seed,
  onApplyGraph,
  onUndo,
  canUndo,
  onModulesCreated,
}: {
  workflowId: string;
  /**
   * A description submitted from the canvas prompt bar. `n` increments per
   * submission so the same text twice still sends; the ref guard keeps a
   * re-render from replaying one.
   */
  seed?: { text: string; n: number } | null;
  /** Apply a copilot graph to the canvas (snapshots the previous one). */
  onApplyGraph: (graph: WorkflowGraph) => void;
  onUndo: () => void;
  canUndo: boolean;
  /** New modules exist server-side — refresh the catalog/palette. */
  onModulesCreated: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const sentSeed = useRef(0);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  const send = async (override?: string) => {
    const message = (override ?? prompt).trim();
    if (!message || busy) return;
    setBusy(true);
    if (!override) setPrompt("");
    setTurns((t) => [...t, { role: "user", content: message }]);
    try {
      const history = turns
        .slice(-10)
        .map((t) => ({ role: t.role, content: t.content }));
      const res: CopilotResponse = await askCopilot(workflowId, message, history);
      const applied = res.graph !== null && res.problems.length === 0;
      if (applied && res.graph) onApplyGraph(res.graph);
      if (res.created_modules.length > 0) onModulesCreated();
      setTurns((t) => [
        ...t,
        {
          role: "assistant",
          content: res.reply || "Done.",
          createdModules: res.created_modules,
          applied,
          problems: res.problems,
        },
      ]);
    } catch (e) {
      setTurns((t) => [
        ...t,
        { role: "assistant",
          content: `That didn't work: ${String(e instanceof Error ? e.message : e)}` },
      ]);
    } finally {
      setBusy(false);
    }
  };

  /* eslint-disable react-hooks/exhaustive-deps */
  // A description typed on the canvas is sent here once, by nonce — `send` is
  // re-created every render, so it must not be a dependency.
  useEffect(() => {
    if (!seed || seed.n === sentSeed.current) return;
    sentSeed.current = seed.n;
    void send(seed.text);
  }, [seed]);
  /* eslint-enable react-hooks/exhaustive-deps */

  return (
    <div className="w-52 sm:w-60 shrink-0 flex flex-col">
      <div className="flex-1 overflow-y-auto scrollbar-thin p-2.5 space-y-2">
        {turns.length === 0 && (
          <div className="text-[10.5px] text-muted-foreground space-y-2 px-1 pt-1">
            <p className="flex items-center gap-1.5 text-foreground font-medium">
              <Icon name="Sparkles" className="w-3.5 h-3.5 text-primary" />
              Build by describing it
            </p>
            <p>
              “When a lead webhook fires, classify it with triage, clean the
              contact rows, and create a follow-up task.”
            </p>
            <p>
              The copilot searches your agents, tools and modules — and if a
              step needs a module that doesn&apos;t exist, it writes, validates
              and saves one for you.
            </p>
          </div>
        )}
        {turns.map((turn, i) => (
          <div key={i}>
            <div
              className={`text-[10.5px] rounded-lg px-2.5 py-2 ${
                turn.role === "user"
                  ? "bg-primary/10 text-foreground ml-4"
                  : "bg-secondary text-foreground mr-2"
              }`}
            >
              {turn.content}
            </div>
            {turn.role === "assistant" && (
              <div className="mt-1 mr-2 space-y-1">
                {(turn.createdModules ?? []).map((m) => (
                  <div
                    key={m.id}
                    className="flex items-center gap-1.5 text-[9.5px] rounded-md border border-sky-500/30 bg-sky-500/10 text-sky-500 px-2 py-1"
                  >
                    <Icon name="Boxes" className="w-3 h-3" />
                    created module <code className="font-semibold">{m.name}</code>
                    — validated &amp; saved
                  </div>
                ))}
                {turn.applied && (
                  <div className="flex items-center gap-2 text-[9.5px] text-muted-foreground px-1">
                    <span className="text-success">● applied to canvas</span>
                    {canUndo && i === turns.length - 1 && (
                      <button
                        onClick={onUndo}
                        className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground underline"
                      >
                        <Icon name="Undo2" className="w-3 h-3" /> undo
                      </button>
                    )}
                    <span>· review, then Save</span>
                  </div>
                )}
                {(turn.problems ?? []).length > 0 && (
                  <div className="text-[9px] text-warning px-1">
                    ⚠ Couldn&apos;t fully validate:{" "}
                    {(turn.problems ?? []).slice(0, 3).join("; ")}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground px-1">
            <Icon name="Loader2" className="w-3 h-3 animate-spin" />
            Searching the catalog and drafting the graph…
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="p-2.5 border-t border-border">
        <div className="flex gap-1.5">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            placeholder="Describe a change…"
            className="flex-1 resize-none rounded-lg border border-input bg-background px-2.5 py-1.5 text-[11px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <Button size="icon" layout="" onClick={() => send()} disabled={busy || !prompt.trim()} title="Send" className="self-end">
            <Icon name="Sparkles" className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}
