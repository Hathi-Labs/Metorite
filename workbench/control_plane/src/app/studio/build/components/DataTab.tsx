"use client";

/**
 * The Data tab of the Studio IDE: the fields of the selected object, read only
 * (`ai_studio.md` §6.3, G3a). Direct edits are G3c.
 */
import { EmptyState } from "@/components/EmptyState";
import SelectButton from "@/components/ui/SelectButton";

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

export interface DataTabProps {
  manifest: unknown;
  object: string | null;
  onObject: (name: string) => void;
}

export default function DataTab({ manifest, object, onObject }: DataTabProps) {
  const objects = isObject(manifest) && Array.isArray(manifest.objects) ? manifest.objects.filter(isObject) : [];
  const named = objects.filter((o) => str(o.name));
  const picked = named.find((o) => str(o.name) === object) ?? named[0];
  if (!picked) {
    return (
      <EmptyState icon="Table" message="This draft has no object yet."
        hint="Ask Mira in the panel on the right what the app should track. Its fields show here." />
    );
  }
  const fields = Array.isArray(picked.fields) ? picked.fields.filter(isObject) : [];
  const rules = Array.isArray(picked.rules) ? picked.rules.filter(isObject) : [];
  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <SelectButton label="Object" value={str(picked.name)} widthClass="w-48"
          options={named.map((o) => ({ value: str(o.name), label: str(o.label) || str(o.name) }))} onChange={onObject} />
        <span className="text-xs text-muted-foreground">Read only. Ask Mira to change a field.</span>
      </div>
      {fields.length === 0 ? (
        <p className="text-xs text-muted-foreground">This object has no field yet. Ask Mira to add fields.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border bg-card">
          <table className="w-full min-w-[28rem] text-left text-sm">
            <caption className="sr-only">Fields of {str(picked.label) || str(picked.name)}</caption>
            <thead className="border-b border-border text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Name</th>
                <th scope="col" className="px-3 py-2 font-medium">Type</th>
                <th scope="col" className="px-3 py-2 font-medium">Required</th>
                <th scope="col" className="px-3 py-2 font-medium">Options</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((f, i) => (
                <tr key={`${str(f.key)}-${i}`} className="border-b border-border last:border-0">
                  <td className="px-3 py-2 text-foreground">{str(f.label) || str(f.key)}
                    {str(f.label) ? <span className="ml-1 text-xs text-muted-foreground">{str(f.key)}</span> : null}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{str(f.type)}{f.many === true ? " (many)" : ""}</td>
                  <td className="px-3 py-2 text-muted-foreground">{f.required === true ? "Yes" : "No"}</td>
                  <td className="break-words px-3 py-2 text-muted-foreground">
                    {Array.isArray(f.options) && f.options.length ? f.options.join(", ") : str(f.to) ? `links to ${str(f.to)}` : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rules.length > 0 ? (
        <section aria-label="Rules" className="rounded-xl border border-border bg-card p-3">
          <h2 className="mb-1 text-sm font-semibold text-foreground">Rules</h2>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {rules.map((r, i) => (
              <li key={i}>{str(r.name) || str(r.kind) || `rule ${i + 1}`}{str(r.kind) && str(r.name) ? ` (${str(r.kind)})` : ""}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
