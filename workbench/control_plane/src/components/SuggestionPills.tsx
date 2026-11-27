"use client";

/**
 * SuggestionPills — starter prompt suggestions shown in the empty state
 * and after each assistant response (CopilotKit-style).
 *
 * Usage:
 *   <SuggestionPills suggestions={["Write a sonnet", "Summarize sales"]} onPick={handleChoice} />
 */

import Icon from "@/components/Icon";

/**
 * A pill with its own words. `label` is what it shows, `message` is what a
 * tap sends, and `icon` is a Lucide name drawn before the label (the starter
 * pills, WS-62 CF-4b: the icon of the card's app).
 */
export interface SuggestionItem {
  label: string;
  message: string;
  icon?: string;
}

interface SuggestionPillsProps {
  suggestions: Array<string | SuggestionItem>;
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
      <div className="text-[10px] text-muted-foreground mb-1.5 uppercase tracking-wide font-medium glass:normal-case glass:tracking-normal glass:text-caption">{label}</div>
      <div className={`flex flex-wrap gap-1.5 ${align === "center" ? "justify-center" : "justify-start"}`}>
        {suggestions.map((raw, i) => {
          const s = typeof raw === "string" ? { label: raw, message: raw } : raw;
          const icon = "icon" in s ? s.icon : undefined;
          return (
          <button
            key={i}
            onClick={() => onPick(s.message)}
            disabled={disabled}
            className="text-[11px] sm:text-xs px-3 py-1.5 rounded-full border border-border bg-secondary/60 text-muted-foreground hover:border-primary/40 hover:bg-primary/10 hover:text-foreground tech-transition disabled:pointer-events-none disabled:opacity-60 glass:border-transparent glass:bg-secondary glass:text-label glass:glass-press glass:hover:border-transparent glass:hover:bg-accent"
          >
            {icon ? <Icon name={icon} size={12} className="mr-1.5 inline-block align-[-2px] shrink-0" aria-hidden /> : null}
            {s.label}
          </button>
          );
        })}
      </div>
    </div>
  );
}
