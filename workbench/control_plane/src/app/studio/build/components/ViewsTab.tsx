"use client";

/**
 * The Views tab of the Studio IDE: the views the draft declares, read only
 * (`ai_studio.md` §6.3, G3a). Direct edits are G3c.
 */
import { EmptyState } from "@/components/EmptyState";
import Badge from "@/components/ui/Badge";

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

export default function ViewsTab({ manifest }: { manifest: unknown }) {
  const views = isObject(manifest) && Array.isArray(manifest.views) ? manifest.views.filter(isObject) : [];
  if (views.length === 0) {
    return (
      <EmptyState icon="LayoutGrid" message="This draft declares no view yet."
        hint="Ask Mira for a Table, a Board or a Form. The views show here." />
    );
  }
  return (
    <ul className="flex flex-col gap-2 p-3">
      {views.map((v, i) => {
        const source = isObject(v.source) ? str(v.source.action) || str(v.source.object) : "";
        const target = str(v.object) || source;
        return (
          <li key={`${str(v.id)}-${i}`} className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card px-3 py-2">
            <span className="min-w-0 break-words text-sm font-medium text-foreground">{str(v.title) || str(v.id) || `View ${i + 1}`}</span>
            <Badge tone="neutral" size="xs">{str(v.block) || "no block"}</Badge>
            {target ? <span className="min-w-0 break-words text-xs text-muted-foreground">shows {target}</span> : null}
            {Array.isArray(v.fields) && v.fields.length ? (
              <span className="min-w-0 break-words text-xs text-muted-foreground">{v.fields.map(String).join(", ")}</span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
