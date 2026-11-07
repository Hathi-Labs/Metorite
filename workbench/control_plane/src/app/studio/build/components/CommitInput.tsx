"use client";

/**
 * A text control that commits on blur and on Enter (STU-G3c fix 1). While the member types, the
 * raw text stays in this control, so a space between two words is kept. The page gets the trimmed
 * text once, when the member leaves the field or presses Enter (`commitText`). Escape puts the stored
 * value back. While the text differs from the stored value, the control tells the page (`pendingText`),
 * so the bar shows "Unsaved changes" and the leave guard is on.
 */
import { useEffect, useState } from "react";

import { Input } from "@/components/ui/Input";
import { commitText } from "@/lib/studio/draftEdit";
import { usePendingText } from "@/lib/studio/pendingText";

export default function CommitInput({ label, value, placeholder, className, disabled, maxLength = 120, onCommit }: {
  label: string; value: string; placeholder?: string; className?: string; disabled?: boolean; maxLength?: number;
  onCommit: (text: string) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const report = usePendingText();
  const pending = text !== null && commitText(value, text) !== null;
  useEffect(() => {
    if (!pending || !report) return;
    report(1);
    return () => report(-1);
  }, [pending, report]);
  const commit = () => {
    if (text === null) return;
    const next = commitText(value, text);
    setText(null);
    if (next !== null) onCommit(next);
  };
  return (
    <form onSubmit={(e) => { e.preventDefault(); commit(); }} className="contents">
      <Input inputSize="sm" className={className} aria-label={label} value={text ?? value} placeholder={placeholder}
        maxLength={maxLength} disabled={disabled} onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Escape") setText(null); }} />
    </form>
  );
}
