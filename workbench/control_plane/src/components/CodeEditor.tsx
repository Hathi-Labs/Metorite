"use client";

/**
 * CodeEditor — the one Monaco editor of the product (STU-G3c, owner directive 2026-11-07: reuse the
 * App Workshop code, do not write it twice).
 *
 * The App Workshop page (`/build/apps/[slug]/edit`) and the Studio IDE Code tab both draw through
 * this wrapper. It owns what every editor here repeated: the lazy load (Monaco is large and has
 * no server render), the theme that follows the product (`useMonacoTheme`), the shared options,
 * and the save key. A caller owns the text, the save and the dirty state.
 *
 *     <CodeEditor value={text} language="json" onChange={setText} onSave={save} />
 *
 * `onSave` binds Ctrl+S and Cmd+S inside the editor, so the browser's own save dialog stays shut.
 * A caller that handles the key on `document` (the Workshop page does) passes none.
 */
import dynamic from "next/dynamic";
import { useEffect, useRef } from "react";

import { useMonacoTheme } from "@/lib/theme/surfaces";

const Monaco = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => <p role="status" className="p-3 text-xs text-muted-foreground">Loading the editor…</p>,
});

type MonacoProps = React.ComponentProps<typeof Monaco>;
export type EditorInstance = Parameters<NonNullable<MonacoProps["onMount"]>>[0];

/** The options that every editor of the product shares. */
export const CODE_EDITOR_OPTIONS = {
  minimap: { enabled: false },
  fontSize: 12.5,
  wordWrap: "on",
  scrollBeyondLastLine: false,
  automaticLayout: true,
} as const;

export interface CodeEditorProps {
  value: string;
  /** Called with the new text on each edit. */
  onChange?: (value: string) => void;
  /** Monaco's language id, such as `json` or `typescript`. */
  language: string;
  readOnly?: boolean;
  /** Ctrl+S or Cmd+S inside the editor. */
  onSave?: () => void;
  /** The editor, once it is on screen. */
  onMount?: (editor: EditorInstance) => void;
}

export default function CodeEditor({ value, onChange, language, readOnly = false, onSave, onMount }: CodeEditorProps) {
  const theme = useMonacoTheme();
  // The save key is bound once, at mount. The ref gives it the latest callback.
  const save = useRef(onSave);
  useEffect(() => { save.current = onSave; });
  return (
    <Monaco
      height="100%"
      language={language}
      theme={theme}
      value={value}
      onChange={onChange ? (v) => onChange(v ?? "") : undefined}
      onMount={(ed, monaco) => {
        ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => save.current?.());
        onMount?.(ed);
      }}
      options={{ ...CODE_EDITOR_OPTIONS, ...(readOnly ? { readOnly: true, domReadOnly: true } : {}) }}
    />
  );
}
