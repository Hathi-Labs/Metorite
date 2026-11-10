// ── The care of a page: owner and review interval (WS-61 KB-AI-4) ──────────
//
// Pure. The choices of the "Review every" form and the words for them. The
// gateway takes 1 to 3650 days, and null for the default (`core.REVIEW_DAYS`,
// 180 days).

/** The days that the gateway uses when a page has no interval. */
export const DEFAULT_REVIEW_DAYS = 180;

/** The form value for "use the default". */
export const DEFAULT_INTERVAL = "default";

const PRESETS: readonly number[] = [30, 90, 180, 365];

/** "30 days", "1 day", "1 year". */
export function intervalLabel(days: number): string {
  if (days === 1) return "1 day";
  if (days === 365) return "1 year";
  return `${days} days`;
}

/** The choices: the default, the presets, and the current value when it is none of them. */
export function intervalOptions(current: number | null): Array<{ value: string; label: string }> {
  const days = [...PRESETS];
  if (current !== null && !days.includes(current)) days.push(current);
  days.sort((a, b) => a - b);
  return [
    { value: DEFAULT_INTERVAL, label: `Default (${intervalLabel(DEFAULT_REVIEW_DAYS)})` },
    ...days.map((d) => ({ value: String(d), label: intervalLabel(d) })),
  ];
}

/** The body value of a form value: null for the default, else the days. */
export function intervalValue(value: string): number | null {
  if (value === DEFAULT_INTERVAL) return null;
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 && n <= 3650 ? n : null;
}
