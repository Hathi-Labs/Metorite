"use client";

/**
 * MeetingPrep — the phase the app didn't have.
 *
 * A meeting moves through three phases: before, during, after. There were
 * surfaces for the last two. Agenda, briefing and copilot context lived only in
 * the live console, which doesn't exist until a session is already running — so
 * the only moment you could tell the copilot what the meeting was about was
 * while it was happening, which is exactly when you can't type. The copilot
 * walked in cold every time.
 *
 * Everything here already existed as an API (`/agenda/chat`, `/brief`,
 * `/context/deep`, `/settings`); what was missing was somewhere to use it
 * before the call. The one genuinely new idea is the context meter: it makes
 * "why is the copilot being generic?" answerable BEFORE the meeting rather than
 * after it.
 */

import Button from "@/components/ui/Button";
import Switch from "@/components/ui/Switch";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  chatAgenda,
  getAgenda,
  getMeetingContext,
  getNotesSettings,
  patchMeeting,
  saveAttendees,
  setDeepContext,
  setMeetingBrief,
  setMeetingTemplate,
  uploadRecording,
} from "../lib/api";
import type {
  AgendaItem,
  Attendee,
  MeetingContext,
  MeetingDetail,
  TemplateInfo,
} from "../lib/types";

/**
 * ISO timestamp → the value a `datetime-local` input expects, in the viewer's
 * own timezone.
 *
 * Slicing the ISO string looks equivalent and isn't: it hands the input a UTC
 * wall-clock time, so a meeting set for 10:00 in IST reopens showing 04:30 —
 * and blurring the field then saves that wrong time back. The offset makes it
 * obvious anywhere but UTC, and invisible in a UTC-based test.
 */
function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(
    d.getHours()
  )}:${p(d.getMinutes())}`;
}

interface Props {
  meeting: MeetingDetail;
  onChanged: () => void;
  /** Open the join-a-call dialog for THIS meeting (owned by the parent so the
   *  modal can live alongside the page's other dialogs). */
  onSendBot: () => void;
}

export default function MeetingPrep({ meeting, onChanged, onSendBot }: Props) {
  const router = useRouter();
  const id = meeting.id;

  const [title, setTitle] = useState(meeting.title ?? "");
  const [scheduled, setScheduled] = useState(toLocalInput(meeting.scheduled_at));
  const [agenda, setAgenda] = useState<AgendaItem[]>([]);
  const [agendaMsg, setAgendaMsg] = useState("");
  const [agendaReply, setAgendaReply] = useState("");
  const [planning, setPlanning] = useState(false);
  const [brief, setBrief] = useState("");
  const [briefSaved, setBriefSaved] = useState(true);
  const [ctx, setCtx] = useState<MeetingContext | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [copilotOn, setCopilotOn] = useState<boolean>(
    meeting.copilot_enabled ?? false
  );
  const [addName, setAddName] = useState("");
  const [addEmail, setAddEmail] = useState("");
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(true);

  const templateKey = meeting.template_key ?? "standard_meeting";
  const attendees = meeting.attendees ?? [];

  const refresh = useCallback(async () => {
    const [a, c] = await Promise.all([
      getAgenda(id).catch(() => [] as AgendaItem[]),
      getMeetingContext(id).catch(() => null),
    ]);
    setAgenda(a);
    if (c) {
      setCtx(c);
      setBrief((prev) => (prev ? prev : c.brief));
    }
    setLoading(false);
  }, [id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    getNotesSettings()
      .then((p) => {
        setTemplates(p.templates ?? []);
        // A meeting that hasn't decided follows the account default — mirror
        // that here so the toggle shows what WOULD happen, not a blank guess.
        if (meeting.copilot_enabled == null) {
          setCopilotOn(Boolean(p.settings?.copilot_default_on));
        }
      })
      .catch(() => {});
  }, [meeting.copilot_enabled]);

  async function savePatch(patch: Parameters<typeof patchMeeting>[1]) {
    try {
      await patchMeeting(id, patch);
      onChanged();
    } catch {
      /* the field keeps the user's value; the next save retries */
    }
  }

  async function onPlan() {
    const msg = agendaMsg.trim();
    if (!msg) return;
    setPlanning(true);
    try {
      const res = await chatAgenda(id, msg);
      setAgenda(res.agenda);
      setAgendaReply(res.reply);
      setAgendaMsg("");
    } catch {
      setAgendaReply("Couldn't reach the copilot — your agenda is unchanged.");
    } finally {
      setPlanning(false);
    }
  }

  async function onSaveBrief() {
    try {
      await setMeetingBrief(id, brief);
      setBriefSaved(true);
      setCtx(await getMeetingContext(id).catch(() => ctx));
    } catch {
      /* left in the box so nothing is lost */
    }
  }

  async function onToggleCopilot() {
    const next = !copilotOn;
    setCopilotOn(next);
    await savePatch({ copilot_enabled: next });
  }

  async function onPickTemplate(key: string) {
    try {
      await setMeetingTemplate(id, key);
      onChanged();
    } catch {
      /* unchanged select reflects the failure */
    }
  }

  function addAttendee() {
    const name = addName.trim();
    const email = addEmail.trim();
    if (!name && !email) return;
    const next = [...attendees, { name, email }];
    setAddName("");
    setAddEmail("");
    void saveAttendees(id, next).then(onChanged).catch(() => {});
  }

  function removeAttendee(i: number) {
    const next = attendees.filter((_, j) => j !== i);
    void saveAttendees(id, next).then(onChanged).catch(() => {});
  }

  async function onUpload(file: File) {
    setUploading(true);
    try {
      await uploadRecording(id, file);
      onChanged();
    } catch {
      setUploading(false);
    }
  }

  // How much the copilot actually has to work with. Three layers, because
  // that's what the context pack assembles: what you told it, what happened
  // before, and what your other agents know.
  const layers = [
    { on: Boolean(brief.trim()), label: "Briefing" },
    { on: (ctx?.past?.length ?? 0) > 0 || attendees.length > 0, label: "History" },
    { on: Object.keys(ctx?.systems ?? {}).length > 0, label: "Agents" },
  ];
  const filled = layers.filter((l) => l.on).length;
  const activeTemplate = templates.find((t) => t.key === templateKey);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Icon name="Loader2" className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* ── Identity ─────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Meeting title
          </label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => title !== (meeting.title ?? "") && savePatch({ title })}
            placeholder="e.g. Acme — pricing review"
            className="w-full rounded-lg border border-border bg-card px-3 py-2 text-sm font-semibold text-foreground focus:border-primary focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            When
          </label>
          <input
            type="datetime-local"
            value={scheduled}
            onChange={(e) => setScheduled(e.target.value)}
            onBlur={() =>
              scheduled && savePatch({ scheduled_at: new Date(scheduled).toISOString() })
            }
            className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none"
          />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
        {/* ── Left: agenda + briefing ─────────────────────────────────── */}
        <div className="space-y-4">
          <section className="rounded-xl border border-border bg-card p-3">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="ListChecks" className="h-3.5 w-3.5" /> Agenda
              {agenda.length > 0 && (
                <span className="font-mono text-[10px] normal-case tracking-normal opacity-70">
                  {agenda.length} item{agenda.length === 1 ? "" : "s"}
                </span>
              )}
            </p>
            {agenda.length > 0 ? (
              <ol className="mb-2 space-y-1.5">
                {agenda.map((item, i) => (
                  <li
                    key={i}
                    className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-sm"
                  >
                    <span className="mr-2 font-mono text-xs text-muted-foreground">
                      {i + 1}
                    </span>
                    {item.title}
                    {item.notes ? (
                      <span className="block pl-6 text-xs text-muted-foreground">
                        {item.notes}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mb-2 text-sm text-muted-foreground">
                Describe the meeting in a line and the copilot drafts an agenda
                you can edit.
              </p>
            )}
            {agendaReply && (
              <p className="mb-2 rounded-lg bg-muted/50 p-2 text-xs text-muted-foreground">
                {agendaReply}
              </p>
            )}
            <div className="flex gap-2">
              <input
                value={agendaMsg}
                onChange={(e) => setAgendaMsg(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !planning && void onPlan()}
                placeholder={
                  agenda.length
                    ? "Refine it — e.g. drop the demo, add pricing"
                    : "e.g. 30-min call with Acme to close the annual renewal"
                }
                className="flex-1 rounded-lg border border-border bg-background px-2.5 py-1.5 text-sm focus:border-primary focus:outline-none"
              />
              <button
                onClick={onPlan}
                disabled={planning || !agendaMsg.trim()}
                className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-secondary disabled:opacity-50"
              >
                {planning ? (
                  <Icon name="Loader2" className="h-4 w-4 animate-spin" />
                ) : agenda.length ? (
                  "Refine"
                ) : (
                  "Draft"
                )}
              </button>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card p-3">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="BookOpen" className="h-3.5 w-3.5" /> Briefing
            </p>
            <textarea
              value={brief}
              onChange={(e) => {
                setBrief(e.target.value);
                setBriefSaved(false);
              }}
              rows={4}
              placeholder="Why this meeting? e.g. Pricing negotiation with Acme. They churned in 2024 over cost. Goal: land the annual contract."
              className="w-full resize-y rounded-lg border border-border bg-background p-2 text-sm focus:border-primary focus:outline-none"
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                onClick={onSaveBrief}
                disabled={briefSaved}
                className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-secondary disabled:opacity-50"
              >
                {briefSaved ? "Saved" : "Save briefing"}
              </button>
              <button
                onClick={async () => {
                  await setDeepContext(id, true).catch(() => {});
                  setCtx(await getMeetingContext(id).catch(() => ctx));
                }}
                title="Ask your CRM, email and task agents for background on these attendees."
                className="rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-secondary"
              >
                Ask CRM + tasks
              </button>
            </div>
          </section>
        </div>

        {/* ── Right: what it will know ────────────────────────────────── */}
        <div className="space-y-4">
          <section className="rounded-xl border border-border bg-card p-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Meeting type
            </p>
            <select
              value={templateKey}
              onChange={(e) => void onPickTemplate(e.target.value)}
              className="mb-2 w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm focus:border-primary focus:outline-none"
            >
              {templates.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
            {/* Showing the lens BEFORE the call is what makes "why did it say
                that?" answerable in advance. */}
            {activeTemplate?.copilot_default && (
              <p className="border-l-2 border-border pl-2 text-[11px] leading-relaxed text-muted-foreground">
                <span className="text-foreground">Copilot lens:</span>{" "}
                {activeTemplate.copilot_default}
              </p>
            )}
          </section>

          <section className="rounded-xl border border-border bg-card p-3">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Users" className="h-3.5 w-3.5" /> Attendees
            </p>
            <div className="mb-2 flex flex-wrap gap-1.5">
              {attendees.map((a: Attendee, i: number) => (
                <span
                  key={`${a.email}-${i}`}
                  className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-1 text-xs"
                  title={a.email}
                >
                  {a.name || a.email}
                  <button
                    onClick={() => removeAttendee(i)}
                    className="text-muted-foreground hover:text-destructive"
                    aria-label={`Remove ${a.name || a.email}`}
                  >
                    <Icon name="X" className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-1.5">
              <input
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
                placeholder="Name"
                className="w-20 flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs focus:border-primary focus:outline-none"
              />
              <input
                value={addEmail}
                onChange={(e) => setAddEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addAttendee()}
                placeholder="email@…"
                className="w-24 flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs focus:border-primary focus:outline-none"
              />
              <button
                onClick={addAttendee}
                className="rounded-md border border-border px-2 text-xs hover:bg-secondary"
              >
                Add
              </button>
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              Attendees are how the copilot finds your past meetings with these
              people.
            </p>
          </section>

          <section className="rounded-xl border border-border bg-card p-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              What the copilot knows
            </p>
            <div className="mb-1.5 flex gap-1">
              {layers.map((l) => (
                <span
                  key={l.label}
                  className={`h-1 flex-1 rounded-full ${
                    l.on ? "bg-success" : "bg-secondary"
                  }`}
                  title={l.label}
                />
              ))}
            </div>
            <p
              className={`mb-2 font-mono text-[11px] ${
                filled === layers.length ? "text-success" : "text-muted-foreground"
              }`}
            >
              {filled} of {layers.length} context layers filled
            </p>
            {ctx && (
              <div className="space-y-1 text-[11px] leading-relaxed text-muted-foreground">
                {ctx.past.slice(0, 2).map((p, i) => (
                  <p key={`p${i}`}>
                    <span className="text-foreground">Previously:</span> {p}
                  </p>
                ))}
                {ctx.open_actions.slice(0, 2).map((a, i) => (
                  <p key={`a${i}`}>
                    <span className="text-foreground">Still open:</span> {a}
                  </p>
                ))}
                {Object.entries(ctx.systems).map(([k, v]) => (
                  <p key={k}>
                    <span className="uppercase text-foreground">{k}:</span> {v}
                  </p>
                ))}
                {filled === 0 && (
                  <p>
                    Nothing yet — a briefing above is the fastest way to make it
                    useful.
                  </p>
                )}
              </div>
            )}
          </section>

          <section className="flex items-center gap-3 rounded-xl border border-border bg-card p-3">
            <Icon name="Sparkles" className="h-4 w-4 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">Meeting copilot</p>
              <p className="text-[11px] text-muted-foreground">
                {copilotOn
                  ? "Starts listening as soon as the meeting does."
                  : "Off — it won't run for this meeting."}
              </p>
            </div>
            <Switch
              checked={copilotOn}
              onChange={() => void onToggleCopilot()}
              label="Meeting copilot"
              classic="outlined"
            />
          </section>
        </div>
      </div>

      {/* ── Start ────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-3">
        <span className="mr-1 text-xs text-muted-foreground">
          Start when you&apos;re ready —
        </span>
        <Button size="none" layout="inline-flex items-center" onClick={() => router.push(`/notes/session/${id}`)} className="gap-1.5 px-3 py-2 text-sm">
          <Icon name="Mic" className="h-4 w-4" /> Record here
        </Button>
        <button
          onClick={onSendBot}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm hover:bg-secondary"
        >
          <Icon name="Video" className="h-4 w-4" /> Send notetaker to a call
        </button>
        <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm hover:bg-secondary">
          {uploading ? (
            <Icon name="Loader2" className="h-4 w-4 animate-spin" />
          ) : (
            <Icon name="Upload" className="h-4 w-4" />
          )}
          {uploading ? "Uploading…" : "Upload a recording"}
          <input
            type="file"
            accept="audio/*,.webm,.m4a,.mp4"
            className="hidden"
            disabled={uploading}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onUpload(f);
              e.target.value = "";
            }}
          />
        </label>
        {filled > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-success">
            <Icon name="Check" className="h-3.5 w-3.5" /> Prepared
          </span>
        )}
      </div>
    </div>
  );
}
