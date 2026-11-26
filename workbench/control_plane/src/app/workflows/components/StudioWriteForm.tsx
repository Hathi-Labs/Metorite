"use client";

/**
 * The form of a Studio write step (WFC-4a-2a, `ai_studio.md` §14).
 *
 * The owner of the workflow picks a package that they own, picks the action
 * that adds a record, and fills the form of that object. The step writes
 * `package_id`, `action` and `data`, and no other key. Every rule lives in
 * `lib/studioWrite.ts`, and this file only draws it.
 *
 * Every choice comes from a read of the package (`listPackages`, then one
 * `getPackage`), and the values come from the Form block, so the member
 * writes no JSON. Another member sees a note and the stored values, and no
 * form. A stored step that the form cannot edit shows a read-only summary.
 */

import { useId, useMemo, useState } from "react";

import { FormView } from "@/components/studio/StudioForm";
import { SelectButton } from "@/components/ui/SelectButton";
import Button from "@/components/ui/Button";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import { blurProblem, type Drafts } from "@/lib/studio/formModel";
import { getPackage, listPackages, type PackageDetail, type PackageSummary } from "@/lib/studio/realDoor";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  LOADING_ROLE_NOTE,
  NOT_OWNER_NOTE,
  STUDIO_WRITE_NOTES,
  actionPatch,
  dataDrafts,
  dataEdit,
  dataFields,
  packagePatch,
  packageView,
  storedSummary,
  studioWriteShape,
  uneditableReason,
  type ShapeEdge,
  type ShapeNode,
  type WriterRole,
} from "../lib/studioWrite";

const LABEL = "text-[10px] font-medium text-muted-foreground uppercase tracking-wide";
const PACKAGES_KEY = "/api/studio/packages";
const PACKAGE_ID = /^[0-9a-fA-F-]{36}$/;

const textOf = (config: Record<string, unknown>, key: string): string =>
  typeof config[key] === "string" ? (config[key] as string) : "";

export default function StudioWriteForm({
  nodeId,
  config,
  onConfig,
  role,
  ownerEmail,
  nodes,
  edges,
}: {
  nodeId: string;
  config: Record<string, unknown>;
  onConfig: (patch: Record<string, unknown>) => void;
  role: WriterRole;
  ownerEmail: string;
  nodes: ShapeNode[];
  edges: ShapeEdge[];
}) {
  const isOwner = role === "owner";
  const packageId = textOf(config, "package_id");
  const action = textOf(config, "action");

  // Only the owner reads the packages. Another member never starts a read.
  const packages = useCachedResource<PackageSummary[]>(isOwner ? PACKAGES_KEY : null, () => listPackages());
  const detailKey = isOwner && PACKAGE_ID.test(packageId) ? `${PACKAGES_KEY}/${packageId}` : null;
  const detailRead = useCachedResource<PackageDetail>(detailKey, () => getPackage(packageId));

  const view = detailRead.data ? packageView(detailRead.data) : detailRead.error && detailKey ? null : undefined;
  const offers = view && view.kind === "ready" ? view.offers : [];
  const offer = offers.find((o) => o.actionId === action) ?? null;
  const fields = useMemo(() => (offer ? dataFields(offer.object) : null), [offer]);
  const known = packages.data ? packages.data.some((p) => p.id === packageId) : undefined;
  const packageName = packages.data?.find((p) => p.id === packageId)?.name;

  const reason = isOwner ? uneditableReason(config, packageId ? known : undefined, view, fields) : null;
  const shape = studioWriteShape(nodeId, nodes, edges);

  const notes = (
    <div className="space-y-1">
      {STUDIO_WRITE_NOTES.map((note) => (
        <p key={note} className="text-[10px] text-muted-foreground">
          {note}
        </p>
      ))}
    </div>
  );

  const shapeNote =
    shape.length > 0 ? (
      <div role="alert" className="space-y-1">
        {shape.map((issue) => (
          <p key={issue.name} className="text-[10px] text-destructive">
            {issue.message}
          </p>
        ))}
      </div>
    ) : (
      <p className="text-[10px] text-muted-foreground">
        This step follows its own Human approval step, so the owner approves each record.
      </p>
    );

  if (!isOwner || reason) {
    const lines = storedSummary(config, packageName);
    return (
      <div className="space-y-3">
        <p role="note" className="text-[10px] text-muted-foreground">
          {role === "loading" ? LOADING_ROLE_NOTE : role === "other" ? ownerNote(ownerEmail) : reason}
        </p>
        <dl className="space-y-1" aria-label="Values of this step">
          {lines.map((line) => (
            <div key={line.label}>
              <dt className={LABEL}>{line.label}</dt>
              <dd className="text-[11px] text-foreground break-words">{line.value}</dd>
            </div>
          ))}
        </dl>
        {notes}
        {shapeNote}
      </div>
    );
  }

  const packageOptions = (packages.data ?? []).map((p) => ({
    value: p.id,
    label: p.name || p.id,
    hint: p.status || undefined,
    keywords: p.name,
  }));

  return (
    <div className="space-y-3">
      <div>
        <span className={LABEL}>Package</span>
        <div className="mt-1">
          {packages.data === undefined ? (
            <div role="status" className="space-y-1.5">
              <p className={`text-[10px] ${packages.error ? "text-destructive" : "text-muted-foreground"}`}>
                {packages.error
                  ? "Could not load your packages. Check the connection, then try again."
                  : "Loading your packages…"}
              </p>
              {packages.error && (
                <Button variant="secondary" size="sm" onClick={packages.refresh}>
                  Try again
                </Button>
              )}
            </div>
          ) : packageOptions.length === 0 ? (
            <p className="text-[10px] text-muted-foreground">
              You own no package yet. Build an app in Studio, then pick it here.
            </p>
          ) : (
            <SelectButton
              label="Package that gets the record"
              value={packageId}
              options={packageOptions}
              onChange={(value) => onConfig(packagePatch(value))}
              prompt="Pick a package"
              filterAbove={6}
              widthClass="w-full"
            />
          )}
        </div>
      </div>

      {detailKey && view === undefined && !detailRead.error && (
        <p role="status" className="text-[10px] text-muted-foreground">
          Loading the package…
        </p>
      )}
      {detailKey && view === null && (
        <p role="alert" className="text-[10px] text-destructive">
          Could not read this package. Pick it again, or pick another.
        </p>
      )}
      {view && view.kind !== "ready" && (
        <p role="alert" className="text-[10px] text-destructive">
          {view.text}
        </p>
      )}

      {offers.length > 0 && (
        <div>
          <span className={LABEL}>Record to add</span>
          <div className="mt-1">
            <SelectButton
              label="Action that adds the record"
              value={action}
              options={offers.map((o) => ({
                value: o.actionId,
                label: `Add a ${o.object.label || o.object.name}`,
                hint: o.actionId,
                keywords: `${o.object.name} ${o.actionId}`,
              }))}
              onChange={(value) => onConfig(actionPatch(value))}
              prompt="Pick an action"
              widthClass="w-full"
            />
          </div>
        </div>
      )}

      {offer && fields && (
        <DataForm
          key={`${packageId}:${action}`}
          fields={fields}
          data={config.data}
          title={`Values for the new ${offer.object.label || offer.object.name}`}
          onApply={(patch) => onConfig(patch)}
        />
      )}

      {notes}
      {shapeNote}
    </div>
  );
}

function ownerNote(ownerEmail: string): string {
  return ownerEmail ? `${NOT_OWNER_NOTE} The owner is ${ownerEmail}.` : NOT_OWNER_NOTE;
}

/** The data form: drafts stay here until the owner presses the button. */
function DataForm({
  fields,
  data,
  title,
  onApply,
}: {
  fields: readonly FieldSpec[];
  data: unknown;
  title: string;
  onApply: (patch: Record<string, unknown>) => void;
}) {
  const formId = useId().replace(/:/g, "");
  const [drafts, setDrafts] = useState<Drafts>(() => dataDrafts(fields, data));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  const setError = (key: string, message: string | null) =>
    setErrors((prev) => {
      const next = { ...prev };
      if (message) next[key] = message;
      else delete next[key];
      return next;
    });

  const submit = () => {
    const result = dataEdit(fields, drafts);
    if (!result.ok) {
      setErrors(result.errors);
      setStatus({ ok: false, text: "The step keeps its old values until each field is right." });
      return;
    }
    setErrors({});
    setStatus({ ok: true, text: "Values set on this step. Save the workflow to keep them." });
    onApply(result.patch);
  };

  return (
    <FormView
      fields={fields}
      drafts={drafts}
      errors={errors}
      formId={formId}
      title={title}
      submitLabel="Use these values"
      status={status}
      onDraft={(key, d) => {
        setDrafts((prev) => ({ ...prev, [key]: d }));
        setStatus(null);
        if (errors[key]) setError(key, blurProblem(fields, { ...drafts, [key]: d }, key));
      }}
      onBlur={(key) => setError(key, blurProblem(fields, drafts, key))}
      onSubmit={submit}
    />
  );
}
