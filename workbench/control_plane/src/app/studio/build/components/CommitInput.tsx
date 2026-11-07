"use client";

/**
 * A text control that commits on blur and on Enter (STU-G3c fix 1). While the member types, the
 * raw text stays in this control, so a space between two words is kept. The page gets the trimmed
 * text once, when the member leaves the field or presses Enter (`commitText`).
 */
import { useState } from "react";

import { Input } from "@/components/ui/Input";
import { commitText } from "@/lib/studio/draftEdit";

export default function CommitInput({ label, value, placeholder, className, disabled, maxLength = 120, onCommit }: {
  label: string; value: string; placeholder?: string; className?: string; disabled?: boolean; maxLength?: number;
  onCommit: (text: string) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    const next = commitText(value, text);
    setText(null);
    if (next !== null) onCommit(next);
  };
  return (
    <form onSubmit={(e) => { e.preventDefault(); commit(); }} className="contents">
      <Input inputSize="sm" className={className} aria-label={label} value={text ?? value} placeholder={placeholder}
        maxLength={maxLength} disabled={disabled} onChange={(e) => setText(e.target.value)} onBlur={commit} />
    </form>
  );
}
