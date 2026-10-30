"use client";

/**
 * SuggestionPills — starter prompt suggestions shown in the empty state
 * and after each assistant response (CopilotKit-style).
 *
 * Usage:
 *   <SuggestionPills suggestions={["Write a sonnet", "Summarize sales"]} onPick={handleChoice} />
 */

interface SuggestionPillsProps {
  suggestions: string[];
  onPick: (suggestion: string) => void;
  /** Show a header label above the pills (default: "Try asking") */
  label?: string;
  /** Pill alignment — "center" for empty state, "start" for follow-ups */
  align?: "center" | "start";
  /** While a pill's act runs, so a second tap does nothing (the reminder chips, RM-4). */
  disabled?: boolean;
}

export default function SuggestionPills({
  suggestions,
  onPick,
  label = "Try asking",
  align = "center",
  disabled = false,
}: SuggestionPillsProps) {
  if (!suggestions.length) return null;

  return (
    <div className="mt-3">
      <div className="text-[10px] text-muted-foreground mb-1.5 uppercase tracking-wide font-medium">{label}</div>
      <div className={`flex flex-wrap gap-1.5 ${align === "center" ? "justify-center" : "justify-start"}`}>
        {suggestions.map((s, i) => (
          <button
            key={i}
            onClick={() => onPick(s)}
            disabled={disabled}
            className="text-[11px] sm:text-xs px-3 py-1.5 rounded-full border border-border bg-secondary/60 text-muted-foreground hover:border-primary/40 hover:bg-primary/10 hover:text-foreground tech-transition disabled:pointer-events-none disabled:opacity-60"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
