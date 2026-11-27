"use client";

/**
 * "Switched to <label>": one line across the transcript where a turn moved to
 * a tool pack that the last turn did not hold (`one_assistant_context.md`
 * §9.4, WT-4). Same idiom and tokens as `MailboxMark`. It offers no tap.
 */

import Icon from "@/components/Icon";
import { packSwitchText } from "@/lib/packSwitch";

export default function PackSwitchMark({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 my-3" data-pack-switch="">
      <div className="flex-1 h-px bg-primary/25" />
      <span className="flex items-center gap-1 text-[10px] text-primary/80 font-medium shrink-0">
        <Icon name="ArrowRightLeft" size={10} /> {packSwitchText(label)}
      </span>
      <div className="flex-1 h-px bg-primary/25" />
    </div>
  );
}
