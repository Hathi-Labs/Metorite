"use client";

/**
 * The Code tab of the Studio IDE: the draft manifest as JSON, read only, in the Monaco editor
 * that the App Workshop page uses (`ai_studio.md` §6.3, G3a). Direct edits are G3c.
 */
import dynamic from "next/dynamic";
import { useMemo } from "react";

import { useMonacoTheme } from "@/lib/theme/surfaces";

const Editor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => <p role="status" className="p-3 text-xs text-muted-foreground">Loading the editor…</p>,
});

export default function CodeTab({ manifest }: { manifest: unknown }) {
  const theme = useMonacoTheme();
  const value = useMemo(() => JSON.stringify(manifest ?? null, null, 2), [manifest]);
  return (
    <div className="h-full min-h-64" data-code-editor>
      <Editor
        height="100%"
        language="json"
        theme={theme}
        value={value}
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
