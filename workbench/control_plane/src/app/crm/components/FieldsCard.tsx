"use client";

/**
 * The Fields card of the CRM settings page (WS-53 CRM-Q4, spec section 18.3).
 *
 * It lists the custom fields of each entity: the label and the type
 * (named as in the field studio), with a flag for a hidden field and the options of a picklist in a
 * disclosure. It is a list to read.
 *
 * **The field studio** (WS-53 CRM-C4, section 20.7). When the layout says
 * `studio_admin`, the card draws `FieldStudio` instead: the flag
 * `CRM_FIELD_STUDIO_ORGS` allows the org, the caller is an admin, and the mode
 * is Native (`fieldsCardShape` in `lib/fieldStudio.ts`). In each other case it
 * draws the list to read. In Mirror mode Zoho owns the fields until CRM-C5, so
 * the note sends the admin to Zoho. The `fields` section is for an admin only,
 * so a member never sees this card.
 */

import Button from "@/components/ui/Button";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import { SkeletonRows } from "@/components/ui/Skeleton";
import type { FieldDef } from "../lib/fields";
import { zohoLink } from "../lib/connect";
import { fieldsCardShape, typeLabel } from "../lib/fieldStudio";
import { useFieldDefs, useFieldLayout, usePipelines } from "../lib/useCrmMode";
import type { EntitySlug } from "../lib/types";
import FieldStudio from "./FieldStudio";
import { Card } from "./SettingsCards";

export const FIELD_ENTITIES: { slug: EntitySlug; label: string }[] = [
  { slug: "leads", label: "Leads" },
  { slug: "contacts", label: "Contacts" },
  { slug: "organizations", label: "Companies" },
  { slug: "deals", label: "Deals" },
];

export const FIELDS_MIRROR_NOTE = "Fields come from Zoho. Edit fields in Zoho.";
export const FIELDS_NATIVE_NOTE = "Field editing is not on for your organization yet.";

/**
 * The Zoho address to link "Edit fields in Zoho" to, or null when none is
 * known. A pipeline of the mirror carries the checked Zoho address of its
 * record (`zohoLink`). The site address is the part up to the first slash.
 */
export function zohoSiteLink(
  pipelines: { ext_url?: string | null }[] | null | undefined,
): string | null {
  for (const pipeline of pipelines ?? []) {
    const link = zohoLink({ ext_url: pipeline.ext_url });
    if (link) return new URL(link).origin;
  }
  return null;
}

/** The label of each option of a picklist. An option is `{label, value}` or a bare value. */
export function optionLabels(options: unknown[] | null): string[] {
  return (options ?? [])
    .map((o) => {
      if (o && typeof o === "object") {
        const named = (o as { label?: unknown; value?: unknown }).label ?? (o as { value?: unknown }).value;
        return typeof named === "string" ? named : "";
      }
      return typeof o === "string" ? o : "";
    })
    .filter(Boolean);
}

/** The card, drawn from definitions that the caller already holds. */
export function FieldsCardView({
  byEntity,
  readOnly,
  zohoUrl = null,
}: {
  byEntity: Record<EntitySlug, FieldDef[] | null>;
  /** Mirror mode. */
  readOnly: boolean;
  /** The checked Zoho address, when known. "Edit fields in Zoho" links to it. */
  zohoUrl?: string | null;
}) {
  return (
    <Card
      title="Fields"
      note={
        !readOnly ? (
          FIELDS_NATIVE_NOTE
        ) : zohoUrl ? (
          <>
            Fields come from Zoho.{" "}
            <a
              href={zohoUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline underline-offset-2"
            >
              Edit fields in Zoho
            </a>
            .
          </>
        ) : (
          FIELDS_MIRROR_NOTE
        )
      }
    >
      <div className="divide-y divide-border/60">
        {FIELD_ENTITIES.map(({ slug, label }) => {
          // A hidden field stays in the list with a flag, so an admin sees it.
          const defs = [...(byEntity[slug] ?? [])].sort((a, b) => a.position - b.position);
          return (
            <div key={slug} aria-label={label} className="px-4 py-3">
              <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
                {label}
              </h3>
              {defs.length === 0 ? (
                <p className="pt-2 text-xs text-muted-foreground">No custom fields yet.</p>
              ) : (
                <ul className="space-y-1.5 pt-2">
                  {defs.map((def) => {
                    const options = optionLabels(def.options);
                    return (
                      <li key={def.key} className="text-xs text-foreground">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{def.label}</span>
                          <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-muted-foreground">
                            {typeLabel(def.type)}
                          </span>
                          {!def.visible && (
                            <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-muted-foreground">
                              hidden
                            </span>
                          )}
                        </div>
                        {options.length > 0 && (
                          <CollapsibleSection
                            label="Options"
                            count={options.length}
                            defaultOpen={false}
                            keepMounted
                          >
                            <ul className="flex flex-wrap gap-1">
                              {options.map((o, i) => (
                                <li
                                  key={`${o}-${i}`}
                                  className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-foreground"
                                >
                                  {o}
                                </li>
                              ))}
                            </ul>
                          </CollapsibleSection>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/**
 * The card with its own reads. The layout read of the first entity says
 * whether the caller gets the studio (CRM-C4c). The studio reads the same
 * entity first, so the two share one cached read. Mirror mode reads no layout.
 */
export default function FieldsCard({ readOnly }: { readOnly: boolean }) {
  const probe = useFieldLayout(readOnly ? null : FIELD_ENTITIES[0].slug);
  const shape = fieldsCardShape(readOnly, probe);
  if (shape === "studio") return <FieldStudio entities={FIELD_ENTITIES} />;
  if (shape === "loading") {
    return (
      <Card title="Fields">
        <SkeletonRows count={4} className="p-4" />
      </Card>
    );
  }
  if (shape === "error") return <FieldsCardError error={probe.error} onRetry={probe.refresh} />;
  return <FieldsCardRead readOnly={readOnly} />;
}

/** The words of a failed layout read (review P2). */
export const FIELDS_READ_FAILED = "The fields could not be read.";

/**
 * A failed layout read. It does not say "Field editing comes later", because
 * the admin may have the studio. Retry reads the layout again.
 */
export function FieldsCardError({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <Card title="Fields">
      <div role="alert" className="flex flex-wrap items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
        <span>
          {FIELDS_READ_FAILED}
          {error ? ` ${error}` : ""}
        </span>
        <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry}>
          Retry
        </Button>
      </div>
    </Card>
  );
}

/** The list to read, with one cached read for each entity. */
function FieldsCardRead({ readOnly }: { readOnly: boolean }) {
  const leads = useFieldDefs("leads").defs;
  const contacts = useFieldDefs("contacts").defs;
  const organizations = useFieldDefs("organizations").defs;
  const deals = useFieldDefs("deals").defs;
  const { pipelines } = usePipelines(readOnly);
  return (
    <FieldsCardView
      readOnly={readOnly}
      zohoUrl={zohoSiteLink(pipelines)}
      byEntity={{ leads, contacts, organizations, deals }}
    />
  );
}

