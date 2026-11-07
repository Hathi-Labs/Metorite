"use client";

/**
 * The Code tab of the Studio IDE: the draft manifest as JSON, in the Monaco editor that the App
 * Workshop page uses (`components/CodeEditor.tsx`, one copy for both). Editable since G3c
 * (`ai_studio.md` §6.3). The text is the member's until they save or revert. A text that does not
 * parse shows its line under the editor, and a save is refused (`draftSave.parseManifestText`).
 */
import { useEffect, useMemo, useRef } from "react";

import CodeEditor, { type EditorInstance } from "@/components/CodeEditor";
import { findLine, type Reveal } from "@/lib/studio/codeReveal";
import type { JsonFault } from "@/lib/studio/draftSave";

export interface CodeTabProps {
  manifest: unknown;
  /** The text that the member typed, or null to show the manifest. */
  text: string | null;
  fault: JsonFault | null;
  reveal?: (Reveal & { seq: number }) | null;
  onText: (text: string) => void;
  onSave: () => void;
}

export default function CodeTab({ manifest, text, fault, reveal, onText, onSave }: CodeTabProps) {
  const pretty = useMemo(() => JSON.stringify(manifest ?? null, null, 2), [manifest]);
  const value = text ?? pretty;
  const editorRef = useRef<EditorInstance | null>(null);
  const shown = useRef(0);
  // Scroll to the item that a click in the Explorer named, once for each click.
  const show = () => {
    const ed = editorRef.current;
    if (!ed || !reveal || reveal.seq === shown.current) return;
    const line = findLine(value, reveal);
    if (line === null) return;
    shown.current = reveal.seq;
    ed.revealLineInCenter(line);
    ed.setPosition({ lineNumber: line, column: 1 });
    ed.setSelection({ startLineNumber: line, startColumn: 1, endLineNumber: line, endColumn: 1000 });
  };
  useEffect(show, [reveal, value]);
  return (
    <div className="flex h-full min-h-64 flex-col">
      <div className="min-h-0 flex-1" data-code-editor>
        <CodeEditor language="json" value={value} onChange={onText} onSave={onSave}
          onMount={(ed) => { editorRef.current = ed; show(); }} />
      </div>
      {fault ? (
        <p role="alert" data-json-fault={fault.line} className="border-t border-border bg-destructive/5 px-3 py-1.5 text-xs text-destructive">
          {fault.message}
        </p>
      ) : null}
    </div>
  );
}
