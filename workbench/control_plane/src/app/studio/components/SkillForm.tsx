"use client";

/**
 * The skill creator form (`ai_studio.md` §14 F2b). One form makes a private
 * skill at `/studio/skills/new` and edits it at `/studio/skills/[id]`.
 *
 * Calls (over the F2a routes, through the `/api/studio` proxy):
 *   New skill        POST  /studio/skills            with a body, so version 1 comes with it
 *   Save version     POST  /studio/skills/{id}/versions
 *   Publish, Resume  PATCH /studio/skills/{id}/state  {state:"published"}
 *   Pause            PATCH /studio/skills/{id}/state  {state:"paused"}
 *
 * Every rule that a test can reach is in `lib/studio/skillForm.ts`, and the
 * place of each error is in `lib/studio/skillErrors.ts`. This file only draws.
 * No rename and no slug change (F2a has no route), no version list, no test
 * chat and no model draft (F2c): see the non-goals of the F2b row.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Input, Textarea } from "@/components/ui/Input";
import {
  EMPTY_FORM,
  MAX_ACTIONS,
  NO_ERRORS,
  classWord,
  createSubmitGuard,
  errorsOf,
  movesOf,
  pickableActions,
  stateTarget,
  toBody,
  toCreate,
  toggleAction,
  valuesOf,
  type FormErrors,
  type FormValues,
} from "@/lib/studio/skillForm";
import { applyStarter, DESCRIPTION_MAX, NAME_MAX, STARTER_MAX } from "@/lib/studio/skillStarter";
import {
  createSkill,
  getSkill,
  listCatalogActions,
  moveSkill,
  saveSkillVersion,
  type CatalogAction,
  type Skill,
} from "@/lib/studio/skillsApi";
import SkillStateBadge from "./SkillStateBadge";

const MOVE_LABEL = { publish: "Publish", pause: "Pause", resume: "Resume" } as const;
const MOVE_ICON = { publish: "Upload", pause: "Pause", resume: "Play" } as const;

function Field({ label, htmlFor, error, hint, children }: {
  label: string;
  htmlFor: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-xs font-medium text-foreground">{label}</label>
      {children}
      {hint && !error && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p role="alert" id={`${htmlFor}-error`} className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

export default function SkillForm({ skillId }: { skillId?: string }) {
  const router = useRouter();
  const guard = useRef(createSubmitGuard()).current;

  const [skill, setSkill] = useState<Skill | null>(null);
  const [values, setValues] = useState<FormValues>(EMPTY_FORM);
  const [saved, setSaved] = useState<FormValues>(EMPTY_FORM);
  const [loading, setLoading] = useState(Boolean(skillId));
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<FormErrors>(NO_ERRORS);
  const [notice, setNotice] = useState<string | null>(null);
  const [sentence, setSentence] = useState("");
  const [catalog, setCatalog] = useState<CatalogAction[]>([]);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let live = true;
    listCatalogActions().then((reply) => {
      if (!live) return;
      if (reply.ok) setCatalog(pickableActions(reply.data.actions ?? []));
      else setCatalogFailed(true);
    });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (!skillId) return;
    let live = true;
    getSkill(skillId).then((reply) => {
      if (!live) return;
      if (reply.ok) {
        const v = valuesOf(reply.data.skill, reply.data.version?.body ?? null);
        setSkill(reply.data.skill);
        setValues(v);
        setSaved(v);
      } else {
        setMissing(true);
      }
      setLoading(false);
    });
    return () => {
      live = false;
    };
  }, [skillId]);

  const titles = useMemo(() => new Map(catalog.map((a) => [a.id, a])), [catalog]);
  const dirty = JSON.stringify(values) !== JSON.stringify(saved);
  const set = (patch: Partial<FormValues>) => {
    setValues((v) => ({ ...v, ...patch }));
    setNotice(null);
  };

  const onStarter = () => {
    setValues((v) => ({ ...v, ...applyStarter(v, sentence) }));
    setNotice(null);
  };

  const onSave = useCallback(async () => {
    await guard.run(async () => {
      setBusy("save");
      setErrors(NO_ERRORS);
      setNotice(null);
      if (!skillId) {
        const reply = await createSkill(toCreate(values));
        if (reply.ok) {
          router.replace(`/studio/skills/${reply.data.skill.id}`);
          return;
        }
        setErrors(errorsOf("create", reply.status, reply.detail));
      } else {
        const reply = await saveSkillVersion(skillId, toBody(values));
        if (reply.ok) {
          setSkill(reply.data.skill);
          setSaved(values);
          setNotice(`Saved as version ${reply.data.version.version}.`);
        } else {
          setErrors(errorsOf("version", reply.status, reply.detail));
        }
      }
      setBusy(null);
    });
  }, [guard, router, skillId, values]);

  const onMove = useCallback(
    async (move: "publish" | "pause" | "resume") => {
      if (!skillId) return;
      await guard.run(async () => {
        setBusy(move);
        setErrors(NO_ERRORS);
        setNotice(null);
        const reply = await moveSkill(skillId, stateTarget(move));
        if (reply.ok) {
          setSkill(reply.data.skill);
          setNotice(move === "publish" ? "Published." : move === "pause" ? "Paused." : "Resumed.");
        } else {
          setErrors(errorsOf("move", reply.status, reply.detail));
          // A 409 means the skill moved under us: load it again.
          if (reply.status === 409) {
            const fresh = await getSkill(skillId);
            if (fresh.ok) setSkill(fresh.data.skill);
          }
        }
        setBusy(null);
      });
    },
    [guard, skillId],
  );

  if (loading) {
    return <p className="p-4 text-sm text-muted-foreground">Loading the skill…</p>;
  }
  if (missing) {
    return (
      <div className="flex flex-col items-start gap-2 p-4">
        <p className="text-sm text-foreground">This skill is not here.</p>
        <Button variant="secondary" size="md" href="/studio">Back to Studio</Button>
      </div>
    );
  }

  const shown = filter.trim().toLowerCase();
  const offered = catalog.filter(
    (a) => !shown || a.title.toLowerCase().includes(shown) || a.id.toLowerCase().includes(shown),
  );
  const moves = skill ? movesOf(skill.state) : [];

  return (
    <div className="pb-askbar flex-1 overflow-y-auto p-3 sm:p-4">
      <form
        className="mx-auto flex max-w-2xl flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void onSave();
        }}
      >
        {skill && (
          <div className="flex flex-wrap items-center gap-2">
            <SkillStateBadge state={skill.state} />
            <span className="text-xs text-muted-foreground">
              {skill.current_version ? `Version ${skill.current_version}` : "No version yet"}
            </span>
            <Badge tone="neutral" icon="Lock">Private</Badge>
          </div>
        )}

        {!skillId && (
          <section aria-label="Starting point" className="flex flex-col gap-2 rounded-xl border border-border bg-card p-3">
            <h2 className="text-sm font-semibold text-foreground">Starting point</h2>
            <p className="text-xs text-muted-foreground">
              Say in one sentence what the skill is for. This fills the empty fields below. No model
              writes this text, and it never changes a field that you typed.
            </p>
            <Textarea
              rows={2}
              value={sentence}
              maxLength={STARTER_MAX}
              aria-label="Describe the skill in one sentence"
              placeholder="Summarise a long email thread for a busy teammate"
              onChange={(e) => setSentence(e.target.value)}
            />
            <div>
              <Button type="button" variant="secondary" size="md" icon="Sparkles" disabled={!sentence.trim()} onClick={onStarter}>
                Fill the form
              </Button>
            </div>
          </section>
        )}

        <Field label="Name" htmlFor="skill-name" error={errors.name}
          hint={skillId ? "The name stays as it is. Only the text below changes in a new version." : undefined}>
          <Input id="skill-name" inputSize="lg" value={values.name} maxLength={NAME_MAX}
            readOnly={Boolean(skillId)} aria-invalid={Boolean(errors.name)}
            onChange={(e) => set({ name: e.target.value })} />
        </Field>

        <Field label="Description (when to use it)" htmlFor="skill-description" error={errors.description}>
          <Textarea id="skill-description" rows={2} value={values.description} maxLength={DESCRIPTION_MAX}
            aria-invalid={Boolean(errors.description)}
            onChange={(e) => set({ description: e.target.value })} />
        </Field>

        <Field label="Instructions" htmlFor="skill-instructions" error={errors.instructions}>
          <Textarea id="skill-instructions" rows={10} value={values.instructions}
            aria-invalid={Boolean(errors.instructions)}
            onChange={(e) => set({ instructions: e.target.value })} />
        </Field>

        <section aria-label="Actions" className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">Actions</h2>
            <span className="text-xs text-muted-foreground">{values.actions.length} of {MAX_ACTIONS}</span>
          </div>
          {errors.picker && <p role="alert" className="text-xs text-destructive">{errors.picker}</p>}

          {values.actions.length > 0 && (
            <ol className="flex flex-col divide-y divide-border rounded-xl border border-border bg-card">
              {values.actions.map((id, index) => {
                const row = titles.get(id);
                return (
                  <li key={id} data-action-row={index} className="flex flex-col gap-1 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm text-foreground">{row?.title ?? id}</span>
                      {row && <Badge tone="neutral" size="sm">{classWord(String(row.class))}</Badge>}
                      <Button type="button" variant="ghost" size="icon-xs" icon="X"
                        aria-label={`Remove ${row?.title ?? id}`}
                        onClick={() => set({ actions: toggleAction(values.actions, id) })} />
                    </div>
                    {errors.actions[index] && (
                      <p role="alert" className="text-xs text-destructive">{errors.actions[index]}</p>
                    )}
                  </li>
                );
              })}
            </ol>
          )}

          <Input aria-label="Search actions" icon="Search" inputSize="md" placeholder="Search actions"
            value={filter} onChange={(e) => setFilter(e.target.value)} />
          {catalogFailed && (
            <p className="text-xs text-muted-foreground">The action list did not load. You can still save the text.</p>
          )}
          <ul className="max-h-64 overflow-y-auto rounded-xl border border-border bg-card">
            {offered.length === 0 && !catalogFailed && (
              <li className="px-3 py-2 text-xs text-muted-foreground">No action matches.</li>
            )}
            {offered.map((a) => {
              const on = values.actions.includes(a.id);
              const full = !on && values.actions.length >= MAX_ACTIONS;
              return (
                <li key={a.id}>
                  <label className="cc-control flex cursor-pointer items-center gap-2 px-3 py-2 hover:bg-secondary">
                    <Checkbox checked={on} disabled={full}
                      onChange={() => set({ actions: toggleAction(values.actions, a.id) })} />
                    <span className="min-w-0 flex-1 truncate text-sm text-foreground">{a.title}</span>
                    <Badge tone="neutral" size="sm">{classWord(String(a.class))}</Badge>
                  </label>
                </li>
              );
            })}
          </ul>
        </section>

        {errors.form && <p role="alert" className="text-sm text-destructive">{errors.form}</p>}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" size="lg" icon="Save" loading={busy === "save"} disabled={busy !== null}>
            {skillId ? "Save version" : "Make skill"}
          </Button>
          {moves.map((m) => (
            <Button key={m} type="button" variant="secondary" size="lg" icon={MOVE_ICON[m]}
              loading={busy === m} disabled={busy !== null || (m === "publish" && dirty)}
              title={m === "publish" && dirty ? "Save a version first. Publish uses the saved text." : undefined}
              onClick={() => void onMove(m)}>
              {MOVE_LABEL[m]}
            </Button>
          ))}
          {notice && (
            <span role="status" className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Icon name="Check" className="h-3.5 w-3.5" />
              {notice}
            </span>
          )}
        </div>
        {errors.move && <p role="alert" className="text-xs text-destructive">{errors.move}</p>}
      </form>
    </div>
  );
}
