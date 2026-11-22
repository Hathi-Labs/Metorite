"use client";

/**
 * The options of a select field, as one comma list (STU-G3c). The text is local while the member
 * types, so a trailing comma is not lost. It commits on blur, and on Enter through the form.
 */
import { useState } from "react";

import { Input } from "@/components/ui/Input";

export default function OptionsInput({ label, options, disabled, onCommit }: {
  label: string; options: readonly string[]; disabled?: boolean; onCommit: (options: string[]) => void;
}) {
  const shown = options.join(", ");
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    const next = text.split(",").map((x) => x.trim()).filter(Boolean);
    setText(null);
    if (next.join(", ") !== shown) onCommit(next);
  };
  return (
    <form onSubmit={(e) => { e.preventDefault(); commit(); }}>
      <Input inputSize="sm" className="w-44" aria-label={label} value={text ?? shown} disabled={disabled}
        placeholder="Option 1, Option 2" onChange={(e) => setText(e.target.value)} onBlur={commit} />
    </form>
  );
}
