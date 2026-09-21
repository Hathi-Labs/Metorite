"use client";

// One credits-per-day sparkline, drawn the same way everywhere.
//
// 🔴 **Extracted from `UsageBoard` for H-133.** The customer page needed the
// same shape, and a second copy of an SVG is a second set of dimensions, a
// second aria-label and a second idea of what an empty series looks like.
// `golive.ts` and `fallback.ts` both record what happens when one row gets
// two verdicts; this is the presentation half of the same rule.
//
// ⚠️ **The judgement stays in `lib/usage.ts`.** `sparklinePath` decides the
// geometry and has its own test. This file only puts it in an `<svg>`.

import { useMemo } from "react";

import { sparklinePath, type UsageDay } from "@/lib/usage";

export default function Spark({
  days,
  width = 560,
  height = 44,
  label = "Credits per day",
}: {
  days: UsageDay[];
  width?: number;
  height?: number;
  label?: string;
}) {
  const d = useMemo(
    () => sparklinePath(days.map((x) => Number(x.credits) || 0), width, height),
    [days, width, height],
  );
  // ⚠️ Null, not an empty box. `sparklinePath` returns null when there is
  // nothing to draw, and a flat line at zero would read as measured quiet.
  if (!d) return null;
  return (
    <svg
      className="spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      <path d={d} />
    </svg>
  );
}
