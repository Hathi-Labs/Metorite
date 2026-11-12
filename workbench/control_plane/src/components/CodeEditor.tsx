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
 * `onSave` binds Ctrl+S and Cmd+S on THIS editor only, so the browser's own save dialog stays shut.
 * The binding is a key listener of the one editor (`onKeyDown`), disposed with it. It is no Monaco
 * command: a command is global, lives on, and would fire a stale save in another editor. A caller
 * that passes no `onSave` binds nothing, so a handler on `document` (the Workshop page has one)
 * still gets the key.
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

/** True for Ctrl+S or Cmd+S. `keyS` is `monaco.KeyCode.KeyS`. */
export function isSaveKey(e: { ctrlKey: boolean; metaKey: boolean; keyCode: number }, keyS: number): boolean {
  return (e.ctrlKey || e.metaKey) && e.keyCode === keyS;
}

export default function CodeEditor({ value, onChange, language, readOnly = false, onSave, onMount }: CodeEditorProps) {
  const theme = useMonacoTheme();
  // The save key is bound once, at mount, and only when a caller wants it. The ref gives it the latest callback.
  const save = useRef(onSave);
  useEffect(() => { save.current = onSave; });
  const bound = useRef<{ dispose: () => void } | null>(null);
  useEffect(() => () => { bound.current?.dispose(); bound.current = null; }, []);
  return (
    <Monaco
      height="100%"
      language={language}
      theme={theme}
      value={value}
      onChange={onChange ? (v) => onChange(v ?? "") : undefined}
      onMount={(ed, monaco) => {
        if (save.current) {
          bound.current = ed.onKeyDown((e) => {
            if (!isSaveKey(e, monaco.KeyCode.KeyS)) return;
            e.preventDefault();
            e.stopPropagation();
            save.current?.();
          });
        }
        onMount?.(ed);
      }}
      options={{ ...CODE_EDITOR_OPTIONS, ...(readOnly ? { readOnly: true, domReadOnly: true } : {}) }}
    />
  );
}
