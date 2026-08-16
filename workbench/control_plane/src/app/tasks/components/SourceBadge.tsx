"use client";

import Badge from "@/components/ui/Badge";
import { ProviderKind, Source } from "../lib/types";
import { sourceBadge } from "../lib/utils";

// A small badge showing whether an item is LOCAL (we own it) or SYNCED
// (mirrors a connected PM tool). Central to the dual-source model (§5.1).
//
// This is a thin wrapper over the shared `Badge` primitive, not its own chip:
// it used to hand-roll the same span with the same tint classes, which meant it
// was themed for COLOUR and frozen for everything else — no `cc-control`, so
// Graphite did not upper-case it and Material did not apply its label tracking,
// and this badge sat next to ones that did. All this file decides now is which
// tone, which icon and what the tooltip says.
export function SourceBadge({
  source,
  provider,
  size = "sm",
}: {
  source: Source;
  provider?: ProviderKind;
  size?: "sm" | "xs";
}) {
  const { label, tone } = sourceBadge(source, provider);
  const local = tone === "local";
  return (
    <Badge
      tone={local ? "neutral" : "primary"}
      size={size}
      icon={local ? "HardDrive" : "Cloud"}
      title={local ? "Local — stored in Metorite" : `Synced — ${label}`}
    >
      {label}
    </Badge>
  );
}
