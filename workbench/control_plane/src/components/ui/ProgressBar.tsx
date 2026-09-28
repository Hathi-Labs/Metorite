/**
 * ProgressBar — a determinate bar for a job the member is waiting on.
 *
 * Tokens only (DESIGN_SYSTEM.md §1): a `bg-muted` track and a `bg-primary`
 * fill, so it follows colour mode and accent like every other surface. It
 * carries `role="progressbar"` and the three `aria-value*` numbers, so a
 * screen reader hears the same number the eye sees.
 *
 * First caller: the Projects file import (WS-41 I-4).
 */

interface Props {
  /** 0 to 100. Clamped. */
  percent: number;
  /** What is progressing, for assistive technology. */
  label: string;
  /** The words beside the number, for example "1,200 of 2,423 tasks". */
  detail?: string;
}

export default function ProgressBar({ percent, label, detail }: Props) {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <div className="flex flex-col gap-1">
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
        className="h-2 w-full overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${value}%` }} />
      </div>
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{detail}</span>
        <span>{value}%</span>
      </div>
    </div>
  );
}
