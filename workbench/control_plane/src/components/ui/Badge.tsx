"use client";

/**
 * Badge — small status pill.
 *
 * Carries the theme's label weight, tracking and transform, so a Graphite
 * badge upper-cases and a Material one picks up its tracking without any call
 * site knowing. Deliberately NOT a button: it takes no click handler, because
 * a clickable badge should be a `<Button size="sm">`.
 *
 *     <Badge tone="success" icon="Check">Connected</Badge>
 *     <Badge>{count}</Badge>
 */

import Icon from "@/components/Icon";

export type BadgeTone = "neutral" | "primary" | "success" | "warning" | "destructive";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-secondary text-muted-foreground",
  primary: "bg-primary/10 text-primary",
  success: "bg-success/10 text-success",
  warning: "bg-warning/10 text-warning",
  destructive: "bg-destructive/10 text-destructive",
};

/**
 * The badge's shape and tone, without its size. `EntityPill` (WS-27bm S9)
 * is built on these classes, so a pill and a badge are one shape. Read it
 * here rather than copying the string, or the two drift apart.
 */
export const BADGE_SHAPE = "inline-flex items-center gap-1 rounded-md";
/** The shape plus `.cc-control`: the themed focus ring and hover layer. */
export const BADGE_BASE = `cc-control ${BADGE_SHAPE}`;

export function badgeTone(tone: BadgeTone = "neutral"): string {
  return TONES[tone];
}

export type BadgeSize = "sm" | "xs";

/**
 * Size is a PROP, not something a call site can pass through `className`.
 * Badge's own `text-[10px]` and a caller's `text-[9px]` are the same property
 * at the same specificity, so which one wins depends on the order Tailwind
 * emitted them in, not on the class attribute — the same trap `Button`'s
 * `layout` prop documents. `xs` is the dense variant used inside list rows and
 * card meta lines, where a full-size badge crowds the row it annotates.
 */
const SIZES: Record<BadgeSize, { chip: string; icon: number }> = {
  sm: { chip: "px-1.5 py-0.5 text-[10px]", icon: 11 },
  xs: { chip: "px-1 py-0.5 text-[9px]", icon: 10 },
};

export type BadgeProps = {
  tone?: BadgeTone;
  size?: BadgeSize;
  /** Lucide icon name rendered before the label. */
  icon?: string;
  /** Renders a filled dot instead of an icon — the app's status convention. */
  dot?: boolean;
  /** Native tooltip. A badge is often the short form of a longer fact. */
  title?: string;
  className?: string;
  children?: React.ReactNode;
};

export default function Badge({
  tone = "neutral",
  size = "sm",
  icon,
  dot = false,
  title,
  className = "",
  children,
}: BadgeProps) {
  const dims = SIZES[size];
  return (
    <span
      title={title}
      className={`${BADGE_BASE} ${dims.chip} ${TONES[tone]} ${className}`}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {icon && <Icon name={icon} size={dims.icon} />}
      {children}
    </span>
  );
}
