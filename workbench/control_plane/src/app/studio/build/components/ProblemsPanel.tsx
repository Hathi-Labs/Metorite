"use client";

/**
 * The Problems panel of the Studio IDE (`ai_studio.md` §6.3, G3a). The header holds the count,
 * as "0 errors · 1 warning". The rows come from `checkDraft`, which runs in the browser.
 * The panel says that the full check runs at a save and a publish.
 *
 * I8a: the lines of the connections of the stored draft come first, each with Connect when it is
 * not met (`ConnectLines`). The header adds "1 connection missing". The gateway decides each line. A
 * read that failed shows one line with Retry, and the header then says the connections were not checked.
 */
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { ConnectLines } from "@/components/studio/ConnectSheet";
import type { ConnectLine } from "@/lib/studio/connect";
import { PANEL_NOTE, problemsLine, type Problem } from "@/lib/studio/draftProblems";

export interface ProblemsPanelProps {
  problems: readonly Problem[];
  open: boolean;
  onToggle: () => void;
  /** Fill the parent, for the phone tab. The desktop panel caps its height. */
  fill?: boolean;
  /** I8a: one line for each connection of the stored draft. */
  connections?: readonly ConnectLine[];
  /** I8a: "1 connection missing", "connections not checked", or null when each one is met. */
  missing?: string | null;
  /** I8a: the line of a read of the connections that failed, or null. */
  failure?: string | null;
  /** I8a: read the connections again. */
  onRetry?: () => void;
}

export default function ProblemsPanel({
  problems, open, onToggle, fill = false, connections = [], missing = null, failure = null, onRetry,
}: ProblemsPanelProps) {
  return (
    <section aria-label="Problems" className={`flex min-h-0 flex-col border-t border-border bg-card ${fill ? "flex-1" : ""}`}>
      <div className="flex items-center gap-2 px-3 py-1">
        <Button type="button" variant="ghost" size="sm" icon={open ? "ChevronDown" : "ChevronRight"}
          aria-expanded={open} onClick={onToggle}>
          Problems
        </Button>
        <span role="status" className="text-xs text-muted-foreground">
          {problemsLine(problems)}{missing ? ` · ${missing}` : ""}
        </span>
      </div>
      {open ? (
        <div className={`overflow-y-auto px-3 pb-2 ${fill ? "flex-1" : "max-h-40"}`}>
          <ConnectLines lines={connections} failure={failure} onRetry={onRetry} />
          {problems.length === 0 ? (
            <p className="text-xs text-muted-foreground">No problem found.</p>
          ) : (
            <ul className="space-y-1">
              {problems.map((p, i) => (
                <li key={`${p.path}-${i}`} className="flex items-start gap-2 text-xs">
                  <Icon name={p.severity === "error" ? "CircleAlert" : "TriangleAlert"} size={14}
                    className={`mt-0.5 shrink-0 ${p.severity === "error" ? "text-destructive" : "text-warning"}`} />
                  <span className="min-w-0 break-words text-foreground">
                    {p.message} <span className="text-muted-foreground">{p.path}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-muted-foreground">{PANEL_NOTE}</p>
        </div>
      ) : null}
    </section>
  );
}
