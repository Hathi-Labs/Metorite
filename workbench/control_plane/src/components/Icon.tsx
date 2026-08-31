"use client";

/**
 * Icon — the icon primitive.
 *
 *     <Icon name="Plus" size={16} className="text-primary" />
 *
 * Lucide names are the vocabulary, and since 2026-08-31 they are also the
 * only pack. The theming engine used to swap the glyph set per theme —
 * Fluent's `fluent:add-20-regular`, Material's `material-symbols:add-rounded`
 * — and that machinery is gone with the themes: no Iconify fetch, no
 * per-pack ready state, no async first paint.
 *
 * ⚠️ **This component is still the rule, and the reason changed.** It is no
 * longer here to abstract over packs; it is here so ~1,400 call sites share
 * one default size, one class contract and one place to fix a glyph. Import
 * this, not `lucide-react` — `conformance.test.ts` rule 2 enforces it.
 *
 * Note that `resolveIcon()` in `@/lib/icons` is the shared lookup, and is
 * also called from server components and from `iconSvg.ts`, which renders to
 * a static string for the HTML sandbox — neither of which can run hooks.
 */

import { createElement } from "react";
import { resolveIcon } from "@/lib/icons";

export type IconProps = {
  /** Lucide icon name, e.g. "Plus", "AlertTriangle", "MessageCircle". */
  name: string;
  /** Edge length in px. Matches Lucide's `size` prop. */
  size?: number;
  className?: string;
  /** Lucide stroke weight. */
  strokeWidth?: number;
  /** SVG paint attributes some call sites set (e.g. a filled star). */
  fill?: string;
  color?: string;
  style?: React.CSSProperties;
  onClick?: React.MouseEventHandler<SVGSVGElement>;
  "aria-label"?: string;
  "aria-hidden"?: boolean;
};

export default function Icon({
  name,
  size = 16,
  className,
  strokeWidth,
  fill,
  color,
  style,
  onClick,
  ...aria
}: IconProps) {
  // createElement rather than JSX: `resolveIcon` LOOKS UP a component from a
  // fixed module map, it does not create one, but rendering the result as
  // `<LucideGlyph />` is indistinguishable from creating a component per render
  // to the lint rule. Same call shape used by genUITemplates and
  // GenerativeUINode for the same reason.
  //
  // ⚠️ `fill` is set ONLY when the caller gave one, and this is not tidiness.
  // Lucide's `Icon` destructures `color`/`size`/`strokeWidth` out and applies
  // `??` defaults to them, but `fill` is not destructured — it rides `...rest`,
  // which is spread AFTER `defaultAttributes` (lucide-react v1.17.0). So
  // `fill: undefined` OVERRIDES their `fill: "none"`, React then omits the
  // attribute entirely, and the SVG default takes over — which is `black`.
  // Every closed path in every icon fills black, in both colour modes.
  // Passing the key unconditionally is what did it. Fence: `Icon.test.ts`.
  const paint = fill === undefined ? {} : { fill };
  return createElement(resolveIcon(name), {
    size,
    className,
    strokeWidth,
    ...paint,
    color,
    style,
    onClick,
    ...aria,
  });
}

/**
 * An icon component bound to one name — what `themedIcon` returns.
 * `displayName` is part of the type so bound icons are identifiable in React
 * DevTools rather than as a wall of anonymous functions.
 */
export type ThemedIcon = ((props: Omit<IconProps, "name">) => React.ReactNode) & {
  displayName?: string;
};

const bound = new Map<string, ThemedIcon>();

/**
 * An icon as a COMPONENT VALUE, for the many places that keep an icon in a
 * lookup table rather than rendering it inline:
 *
 *     const META = { inbox: { icon: themedIcon("Inbox"), label: "Inbox" } };
 *     …
 *     <META.inbox.icon size={16} />
 *
 * ⚠️ The name is a fossil: it dates from when this resolved a glyph per
 * theme. It is kept rather than renamed to `boundIcon` because ~90 call
 * sites read it and the rename would be pure churn in a diff that is already
 * deleting an engine. What it means now is "an icon through the one seam".
 *
 * Results are memoised per name because the return value is a component TYPE.
 * A fresh function on every call would be a new type each render, and React
 * would unmount and remount the icon instead of updating it.
 */
export function themedIcon(name: string): ThemedIcon {
  const cached = bound.get(name);
  if (cached) return cached;
  const Bound: ThemedIcon = (props) => <Icon name={name} {...props} />;
  Bound.displayName = `ThemedIcon(${name})`;
  bound.set(name, Bound);
  return Bound;
}
