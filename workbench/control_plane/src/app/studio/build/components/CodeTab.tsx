"use client";

/**
 * The Code tab of the Studio IDE: the draft manifest as JSON, read only, in the Monaco editor
 * that the App Workshop page uses (`ai_studio.md` §6.3, G3a). Direct edits are G3c.
 */
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef } from "react";

import { findLine, type Reveal } from "@/lib/studio/codeReveal";

import { useMonacoTheme } from "@/lib/theme/surfaces";

const Editor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => <p role="status" className="p-3 text-xs text-muted-foreground">Loading the editor…</p>,
});

type Mounted = Parameters<NonNullable<React.ComponentProps<typeof Editor>["onMount"]>>[0];

export default function CodeTab({ manifest, reveal }: { manifest: unknown; reveal?: (Reveal & { seq: number }) | null }) {
  const theme = useMonacoTheme();
  const value = useMemo(() => JSON.stringify(manifest ?? null, null, 2), [manifest]);
  const editorRef = useRef<Mounted | null>(null);
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
  useEffect(show, [reveal, value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="h-full min-h-64" data-code-editor>
      <Editor
        height="100%"
        language="json"
        theme={theme}
        value={value}
        onMount={(ed) => { editorRef.current = ed; show(); }}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 12.5,
          wordWrap: "on",
          scrollBeyondLastLine: false,
          automaticLayout: true,
          domReadOnly: true,
        }}
      />
    </div>
  );
}
