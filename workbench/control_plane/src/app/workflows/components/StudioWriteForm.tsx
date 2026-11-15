"use client";

/**
 * The form of a Studio write step (WFC-4a-2a and WFC-4a-2b-1, `ai_studio.md` §14).
 *
 * The owner of the workflow picks a package that they own and the action. A
 * create action takes the values of a new record from the Form block. A change
 * action takes the record from a picker, and the fields that the owner adds.
 * Every rule lives in `lib/studioWrite.ts`, and this file only draws it.
 *
 * Every choice comes from a read: `listPackages`, then one `getPackage`, then
 * the records through a door of the READ actions only (`readActions`). The
 * form never runs an action of the door, and it writes no JSON and takes no
 * typed id.
 * Another member sees a note and the stored values, with the record by its id
 * only, and starts no read. A stored step that the form cannot edit shows a
 * read-only summary.
 */

import { useId, useMemo, useState } from "react";

import { FormView } from "@/components/studio/StudioForm";
import { SelectButton } from "@/components/ui/SelectButton";
import Button from "@/components/ui/Button";
import { labelOf } from "@/lib/studio/blocks";
import { moreLine, rowsMeta, withMeta, type Row, type StudioDataDoor, type StudioSource } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import { blurProblem, type Drafts } from "@/lib/studio/formModel";
import { fieldSpecs, recordTitle } from "@/lib/studio/packagePreview";
import {
  createDoorKeeper,
  getPackage,
  listPackages,
  readActions,
  type ObjectRow,
  type PackageDetail,
  type PackageSummary,
} from "@/lib/studio/realDoor";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  GONE_RECORD_MESSAGE,
  LOADING_ROLE_NOTE,
  NOT_OWNER_NOTE,
  NO_FIELD_LINE,
  NO_RECORDS_LINE,
  RECORD_PAGE,
  SAVED_FAILED_MESSAGE,
  STUDIO_WRITE_NOTES,
  actionPatch,
  addField,
  addableFields,
  canGetRecord,
  dataDrafts,
  dataEdit,
  fieldsFor,
  initialAdded,
  isRecordId,
  nextOffset,
  offerLabel,
  packagePatch,
  packageView,
  recordGetSource,
  recordListSource,
  recordOptions,
  recordPatch,
  recordReadKey,
  recordsFailedText,
  removeField,
  savedRecordState,
  stepNote,
  storedSummary,
  studioWriteShape,
  uneditableReason,
  unlistedLine,
  updateEdit,
  type PackageView,
  type ShapeEdge,
  type ShapeNode,
  type WriterRole,
} from "../lib/studioWrite";

const LABEL = "text-[10px] font-medium text-muted-foreground uppercase tracking-wide";
const PACKAGES_KEY = "/api/studio/packages";
const PACKAGE_ID = /^[0-9a-fA-F-]{36}$/;
const SET_NOTE = "Values set on this step. Save the workflow to keep them.";
const KEPT_NOTE = "The step keeps its old values until each field is right.";

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
  const recordId = textOf(config, "record_id");

  // Only the owner reads the packages. Another member never starts a read.
  const packages = useCachedResource<PackageSummary[]>(isOwner ? PACKAGES_KEY : null, () => listPackages());
  const detailKey = isOwner && PACKAGE_ID.test(packageId) ? `${PACKAGES_KEY}/${packageId}` : null;
  const detailRead = useCachedResource<PackageDetail>(detailKey, () => getPackage(packageId));

  const view = useMemo<PackageView | null | undefined>(
    () => (detailRead.data ? packageView(detailRead.data) : detailRead.error && detailKey ? null : undefined),
    [detailRead.data, detailRead.error, detailKey],
  );
  const offers = useMemo(() => (view && view.kind === "ready" ? view.offers : []), [view]);
  const offer = offers.find((o) => o.actionId === action) ?? null;
  const fields = useMemo(() => (offer ? fieldsFor(offer) : null), [offer]);
  const known = packages.data ? packages.data.some((p) => p.id === packageId) : undefined;
  const packageName = packages.data?.find((p) => p.id === packageId)?.name;

  // THE READ DOOR (WFC-4a-2b-1). It holds the READ actions of the package only,
  // so no read can reach a write, and only the owner of an owned package gets one.
  const [keeper] = useState(() => createDoorKeeper());
  const owned = isOwner && view && view.kind === "ready" && detailRead.data ? detailRead.data : null;
  const door = owned ? keeper(packageId, readActions(owned.actions)) : null;

  const reason = isOwner ? uneditableReason(config, packageId ? known : undefined, view, fields) : null;
  const shape = studioWriteShape(nodeId, nodes, edges);
  const note = offer ? stepNote(offer.verb, config) : null;
  const apply = (patch: Record<string, unknown>) => onConfig(patch);

  const notes = (
    <div className="space-y-1">
      {STUDIO_WRITE_NOTES.map((line) => (
        <p key={line} className="text-[10px] text-muted-foreground">
          {line}
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
  const unlisted = view && view.kind !== "viewer" ? unlistedLine(view.unlisted) : null;
  const objectName = offer ? offer.object.label || offer.object.name : "";

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
          <span className={LABEL}>Action</span>
          <div className="mt-1">
            <SelectButton
              label="Action that writes the record"
              value={action}
              options={offers.map((o) => ({
                value: o.actionId,
                label: offerLabel(o),
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
      {unlisted && <p className="text-[10px] text-muted-foreground">{unlisted}</p>}

      {offer && offer.verb === "update" && (
        <RecordPicker
          key={`picker:${packageId}:${action}`}
          role={role}
          door={door}
          object={offer.object}
          canGet={canGetRecord(owned?.actions, offer.object.name)}
          recordId={recordId}
          onPick={(value) => onConfig(recordPatch(value))}
        />
      )}
      {note && (
        <p role="note" className="text-[10px] text-muted-foreground">
          {note}
        </p>
      )}

      {offer && fields && offer.verb === "update" && (
        <UpdateForm
          key={`change:${packageId}:${action}`}
          fields={fields}
          data={config.data}
          title={`New values for the ${objectName}`}
          onApply={apply}
        />
      )}
      {offer && fields && offer.verb === "create" && (
        <DataForm
          key={`create:${packageId}:${action}`}
          fields={fields}
          data={config.data}
          title={`Values for the new ${objectName}`}
          onApply={apply}
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

/**
 * The record picker of a change (WFC-4a-2b-1). It reads the records 100 at a
 * time through the read door, and names each by its title. The step keeps the
 * id only. A saved record that the loaded rows do not hold gets ONE get read,
 * for its title, when the package declares a get.
 */
function RecordPicker({
  role,
  door,
  object,
  canGet,
  recordId,
  onPick,
}: {
  role: WriterRole;
  door: StudioDataDoor | null;
  object: ObjectRow;
  canGet: boolean;
  recordId: string;
  onPick: (recordId: string) => void;
}) {
  const fields = useMemo(() => fieldSpecs(object), [object]);
  const [earlier, setEarlier] = useState<Row[]>([]);
  const [offset, setOffset] = useState(0);
  const listSource = useMemo(() => recordListSource(object.name, offset), [object.name, offset]);
  const page = useCachedResource<Row[]>(recordReadKey(role, door, listSource), () => readThrough(door, listSource));

  const loaded = useMemo(() => [...earlier, ...(page.data ?? [])], [earlier, page.data]);
  const inList = loaded.some((r) => String(r.id) === recordId);
  const settled = page.data !== undefined || !!page.error;
  const getSource = canGet && isRecordId(recordId) && settled && !inList ? recordGetSource(object.name, recordId) : null;
  const getKey = recordReadKey(role, door, getSource);
  const saved = useCachedResource<Row[]>(getKey, () => readThrough(door, getSource));
  const savedState = savedRecordState({ key: getKey, data: saved.data, error: saved.error });
  const savedTitle = savedState === "found" && saved.data ? recordTitle(fields, saved.data[0]) : null;

  const options = recordOptions(fields, loaded, recordId, savedTitle);
  const more = page.data ? nextOffset(offset, page.data.length) : null;
  const line = page.data ? moreLine(withMeta([...loaded], rowsMeta(page.data) ?? {})) : null;
  const firstLoad = earlier.length === 0;

  return (
    <div>
      <span className={LABEL}>Record to change</span>
      <div className="mt-1 space-y-1.5">
        {page.data === undefined && page.error && firstLoad ? (
          <div role="status" className="space-y-1.5">
            <p className="text-[10px] text-destructive">{recordsFailedText(page.error)}</p>
            <Button variant="secondary" size="sm" onClick={page.refresh}>
              Try again
            </Button>
          </div>
        ) : page.data === undefined && firstLoad ? (
          <p role="status" className="text-[10px] text-muted-foreground">
            Loading the records…
          </p>
        ) : options.length === 0 ? (
          <p className="text-[10px] text-muted-foreground">{NO_RECORDS_LINE}</p>
        ) : (
          <SelectButton
            label="Record that this step changes"
            value={recordId}
            options={options}
            onChange={onPick}
            prompt="Pick a record"
            filterAbove={6}
            widthClass="w-full"
          />
        )}
        {savedState === "gone" && (
          <p role="alert" className="text-[10px] text-destructive">
            {GONE_RECORD_MESSAGE}
          </p>
        )}
        {savedState === "failed" && (
          <div role="status" className="space-y-1.5">
            <p className="text-[10px] text-destructive">{SAVED_FAILED_MESSAGE}</p>
            <Button variant="secondary" size="sm" onClick={saved.refresh}>
              Try again
            </Button>
          </div>
        )}
        {line && (
          <p role="status" className="text-[10px] text-muted-foreground">
            {line}
          </p>
        )}
        {!firstLoad && page.data === undefined && !page.error && (
          <p role="status" className="text-[10px] text-muted-foreground">
            Loading more records…
          </p>
        )}
        {!firstLoad && page.data === undefined && page.error && (
          <div role="status" className="space-y-1.5">
            <p className="text-[10px] text-destructive">{recordsFailedText(page.error)}</p>
            <Button variant="secondary" size="sm" onClick={page.refresh}>
              Try again
            </Button>
          </div>
        )}
        {more !== null && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setEarlier(loaded);
              setOffset(more);
            }}
          >
            {`Show ${RECORD_PAGE} more records`}
          </Button>
        )}
      </div>
    </div>
  );
}

/** A read through the read door. The key is null without a door, so this runs only with one. */
function readThrough(door: StudioDataDoor | null, source: StudioSource | null): Promise<Row[]> {
  if (!door || !source) return Promise.reject(new Error(SAVED_FAILED_MESSAGE));
  return door.read(source);
}

/**
 * The data form of a change: it starts with no field. The owner adds each
 * field to change, and only those go into `data`. Drafts stay here until the
 * owner presses the button.
 */
function UpdateForm({
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
  const [added, setAdded] = useState<string[]>(() => initialAdded(fields, data));
  const [drafts, setDrafts] = useState<Drafts>(() => dataDrafts(fields, data));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  const keep = new Set(added);
  const shown = fields.filter((f) => keep.has(f.key));
  const addable = addableFields(fields, added);

  const setError = (key: string, message: string | null) =>
    setErrors((prev) => {
      const next = { ...prev };
      if (message) next[key] = message;
      else delete next[key];
      return next;
    });

  const submit = () => {
    const result = updateEdit(fields, added, drafts);
    if (!result.ok) {
      setErrors(result.errors);
      setStatus({ ok: false, text: result.message ?? KEPT_NOTE });
      return;
    }
    setErrors({});
    setStatus({ ok: true, text: SET_NOTE });
    onApply(result.patch);
  };

  return (
    <div className="space-y-2">
      <div>
        <span className={LABEL}>Fields to change</span>
        <div className="mt-1 space-y-1.5">
          {added.length === 0 && <p className="text-[10px] text-muted-foreground">{NO_FIELD_LINE}</p>}
          {addable.length > 0 && (
            <SelectButton
              label="Add a field to change"
              value=""
              options={addable.map((f) => ({ value: f.key, label: labelOf(f) }))}
              onChange={(key) => {
                setAdded(addField(fields, added, key));
                setStatus(null);
              }}
              prompt="Add a field"
              filterAbove={6}
              widthClass="w-full"
            />
          )}
        </div>
      </div>
      <FormView
        fields={shown}
        drafts={drafts}
        errors={errors}
        formId={formId}
        title={title}
        submitLabel="Use these values"
        status={status}
        fieldAction={(f) => (
          <Button
            variant="ghost"
            size="sm"
            icon="X"
            aria-label={`Remove ${labelOf(f)} from this step`}
            onClick={() => {
              setAdded(removeField(added, f.key));
              setError(f.key, null);
              setStatus(null);
            }}
          >
            Remove
          </Button>
        )}
        onDraft={(key, d) => {
          setDrafts((prev) => ({ ...prev, [key]: d }));
          setStatus(null);
          if (errors[key]) setError(key, blurProblem(fields, { ...drafts, [key]: d }, key));
        }}
        onBlur={(key) => setError(key, blurProblem(fields, drafts, key))}
        onSubmit={submit}
      />
    </div>
  );
}

/** The data form of a create: drafts stay here until the owner presses the button. */
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
      setStatus({ ok: false, text: KEPT_NOTE });
      return;
    }
    setErrors({});
    setStatus({ ok: true, text: SET_NOTE });
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
