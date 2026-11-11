"use client";

/**
 * Settings → Assistant settings (WS-63 PZ-7, `personalization.md` §4.3, §7).
 *
 * The member sees and controls everything the assistant keeps about them:
 * one learning switch, how the assistant addresses them, how it answers,
 * every fact with its source and date, the name shortcuts, and "Forget
 * everything".
 *
 * `AssistantPaneView` is a pure view (props to markup), so a test draws it
 * with `renderToStaticMarkup`. `AssistantPane` is the container: it loads,
 * calls the proxies in `lib/profile.ts`, and holds the busy and error state
 * per row.
 *
 * "Off" stops the assistant writing. It still reads. The pane says so.
 * ⚠️ A failed load is an error with Retry, never the empty form: an empty
 * form on a 503 would invite a save over a profile the member has.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Badge, { type BadgeTone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input, Textarea } from "@/components/ui/Input";
import { SelectButton } from "@/components/ui/SelectButton";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { peopleApi } from "@/app/people/lib/api";
import { saveBlob } from "@/lib/export";
import { startGetToKnowMe } from "@/lib/shell/interviewMessage";

import {
  CAPS,
  FACT_KINDS,
  INTERVIEW_AREAS,
  STATE_LABELS,
  FORMALITIES,
  GREETINGS,
  KIND_LABELS,
  LANGUAGES,
  LENGTHS,
  MONTHS,
  ProfileError,
  addAlias,
  addFact,
  addLanguage,
  areaLabel,
  celebrationsGate,
  chatHref,
  daysInMonth,
  deleteAlias,
  DELETE_ONE_BODY,
  deleteAllBody,
  deleteAllSummaries,
  deleteFact,
  deleteSummary,
  errorText,
  fallbackAddress,
  fetchBlock,
  fetchExport,
  fetchProfileAndGate,
  fetchSummaries,
  forgetAll,
  mergeAddress,
  mergeCelebrations,
  languageName,
  languagesToAdd,
  mergeStyle,
  moveLanguage,
  nearCap,
  patchAlias,
  patchFact,
  reconcileDraft,
  removeLanguage,
  resetArea,
  resetInterview,
  saveAddress,
  saveCelebrations,
  saveKeepSummaries,
  saveLearning,
  saveStyle,
  styleLanguages,
  shortDate,
  sourceLabel,
  withoutArea,
  withoutInterview,
  type Address,
  type Alias,
  type CelebrationsGate,
  type ChatSummary,
  type ProfileBlock,
  type SummariesPage,
  type SummaryGate,
  type ExportFormat,
  type Fact,
  type FactKind,
  type InterviewAreaId,
  type InterviewState,
  type Profile,
  type Style,
} from "./lib/profile";

/** Handlers return true when the call worked, so a form may clear its draft. */
export interface PaneHandlers {
  onLearning: (value: boolean) => Promise<boolean>;
  onAddress: (edit: Parameters<typeof mergeAddress>[1]) => Promise<boolean>;
  onStyle: (edit: Parameters<typeof mergeStyle>[1]) => Promise<boolean>;
  onAddFact: (text: string, kind: FactKind, area?: InterviewAreaId) => Promise<boolean>;
  onEditFact: (id: string, patch: { text: string } | { kind: FactKind }) => Promise<boolean>;
  onDeleteFact: (id: string) => Promise<boolean>;
  onAddAlias: (alias: string, email: string) => Promise<boolean>;
  onEditAlias: (id: string, patch: { alias: string } | { email: string }) => Promise<boolean>;
  onDeleteAlias: (id: string) => Promise<boolean>;
  onForget: () => void;
  onRetry: () => void;
  /** Load the text again after a failed load (PZ-20a). */
  onRetryBlock: () => void;
  /** Download the member's own profile (PZ-9). */
  onExport: (format: ExportFormat) => Promise<boolean>;
  /** Open a fresh chat and send the Get to know me request (PZ-27). */
  onGetToKnow: () => void;
  /** Reset one interview area. The facts stay. */
  onResetArea: (id: InterviewAreaId) => Promise<boolean>;
  /** Ask to reset the whole interview. A confirm dialog follows. */
  onResetInterview: () => void;
  /** Save the birthday, the festival picks or the work-anniversary switch (PZ-43). */
  onCelebrations: (edit: Parameters<typeof mergeCelebrations>[1]) => Promise<boolean>;
  /** The switch "Keep chat summaries" (PZ-35). */
  onKeepSummaries: (value: boolean) => Promise<boolean>;
  /** Ask to delete the summary of one chat. A confirm dialog follows (PZ-35). */
  onDeleteSummary: (id: string) => void;
  /** Ask to delete every summary. A confirm dialog follows (PZ-35). */
  onDeleteAllSummaries: () => void;
  /** Load the list again after a failed load (PZ-35). */
  onRetrySummaries: () => void;
}

/** The list of "Recent chats" (PZ-35). */
export type SummariesList =
  | { status: "loading" }
  | { status: "error"; error: string }
  | ({ status: "ready" } & SummariesPage);

/** The section "Recent chats": the member's switch and the list (PZ-35). */
export interface SummariesView {
  keep: boolean;
  list: SummariesList;
}

/** The state of "What the assistant sees" (PZ-20a). */
export type BlockState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | ({ status: "ready" } & ProfileBlock);

export interface AssistantPaneViewProps extends PaneHandlers {
  status: "loading" | "error" | "ready";
  /** The load error, when `status` is "error". */
  loadError?: string;
  profile: Profile;
  /** Keys: "learning", "address", "style", "new-fact", "new-alias", or a row id. */
  busy: Record<string, boolean>;
  errors: Record<string, string>;
  confirmForget: boolean;
  onCancelForget: () => void;
  onConfirmForget: () => void;
  confirmResetInterview: boolean;
  onCancelResetInterview: () => void;
  onConfirmResetInterview: () => void;
  /**
   * Counts each Forget everything. A new value resets every draft field to the
   * server value, so no old text stays in an input with Save enabled.
   */
  epoch?: number;
  /** The name the assistant uses while the Name field is empty, when known. */
  fallbackName?: { name: string; from: "preferred" | "display" } | null;
  /** "What the assistant sees". Without it, the section does not draw. */
  block?: BlockState;
  /** "Celebrations" (PZ-43). Without it, the section does not draw (the flag is off). */
  celebrations?: CelebrationsGate;
  /** "Recent chats". Without it, the section does not draw (the flag is off). */
  summaries?: SummariesView;
  confirmDeleteSummaries?: boolean;
  onCancelDeleteSummaries?: () => void;
  /** The chat whose summary waits on its confirm, or null. */
  confirmDeleteSummaryId?: string | null;
  onCancelDeleteSummary?: () => void;
  onConfirmDeleteSummary?: () => void;
  onConfirmDeleteSummaries?: () => void;
}

const noop = async () => true;
export const NO_HANDLERS: PaneHandlers = {
  onLearning: noop,
  onAddress: noop,
  onStyle: noop,
  onAddFact: noop,
  onEditFact: noop,
  onDeleteFact: noop,
  onAddAlias: noop,
  onEditAlias: noop,
  onDeleteAlias: noop,
  onForget: () => {},
  onRetry: () => {},
  onRetryBlock: () => {},
  onExport: noop,
  onGetToKnow: () => {},
  onResetArea: noop,
  onResetInterview: () => {},
  onCelebrations: noop,
  onKeepSummaries: noop,
  onDeleteSummary: () => {},
  onDeleteAllSummaries: () => {},
  onRetrySummaries: () => {},
};

/** The jump links at the top, and the id each section answers to. */
const JUMPS = [
  { id: "assistant-memory", label: "Memory" },
  { id: "assistant-address", label: "How I address you" },
  { id: "assistant-style", label: "How I answer" },
  { id: "assistant-facts", label: "What I remember" },
  { id: "assistant-interview", label: "Get to know me" },
  { id: "assistant-names", label: "Names for people" },
  { id: "assistant-block", label: "What it sees" },
  { id: "assistant-download", label: "Download" },
  { id: "assistant-forget", label: "Forget" },
] as const;


/** The jump links of the sections that draw. "Celebrations" and "Recent chats" come only with their flags. */
function jumpsFor(v: { summaries?: unknown; celebrations?: unknown }) {
  const out: { id: string; label: string }[] = [...JUMPS];
  if (v.celebrations) out.splice(3, 0, { id: "assistant-celebrations", label: "Celebrations" });
  if (v.summaries) {
    const at = out.findIndex((j) => j.id === "assistant-download");
    out.splice(at, 0, { id: "assistant-summaries", label: "Recent chats" });
  }
  return out;
}

function Section({
  id,
  title,
  description,
  children,
}: {
  id?: string;
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {description ? <p className="mt-0.5 text-xs text-muted-foreground">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

/** The words for an empty text (PZ-20a). UI copy only: the server sends "". */
export const BLOCK_EMPTY = "The assistant sees nothing about you yet.";

/**
 * "What the assistant sees" (PZ-20a). The text is the profile block that the
 * assistant reads before a chat, word for word. Read only.
 */
function BlockSection({ block, onRetry }: { block: BlockState; onRetry: () => void }) {
  if (block.status === "ready" && !block.enabled) return null;
  return (
    <Section
      id="assistant-block"
      title="What the assistant sees"
      description="The exact text the assistant reads about you before a chat. Other members never see it."
    >
      {block.status === "loading" ? (
        <div data-testid="assistant-block-loading">
          <Skeleton className="h-24 w-full" />
        </div>
      ) : block.status === "error" ? (
        <div role="alert" className="space-y-2 rounded-lg border border-border bg-card p-3">
          <p className="text-xs text-foreground">The text did not load.</p>
          <p className="text-xs text-muted-foreground">{block.error || "Try again."}</p>
          <Button size="sm" variant="secondary" icon="RefreshCw" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : block.text === "" ? (
        <p className="text-xs text-muted-foreground">{BLOCK_EMPTY}</p>
      ) : (
        <pre
          aria-label="The text the assistant reads about you"
          data-testid="assistant-block-text"
          className="whitespace-pre-wrap break-words rounded-lg border border-border bg-card p-3 font-mono text-xs text-foreground"
        >
          {block.text}
        </pre>
      )}
      <p className="text-xs text-muted-foreground">
        In a chat, the assistant can add one reminder line to this text.
      </p>
    </Section>
  );
}

/** The date of a chat, or "" when the gateway sent none. */
function chatDate(date: string | null): string {
  return date ? shortDate(date) : "";
}

function SummaryRow({ s, v }: { s: ChatSummary; v: AssistantPaneViewProps }) {
  const busy = v.busy[`summary-${s.id}`];
  const when = chatDate(s.date);
  const title = s.title || "Untitled chat";
  return (
    <li className="space-y-1.5 rounded-lg border border-border bg-card px-3 py-2">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Link href={chatHref(s.id)} className="text-sm font-medium text-foreground underline-offset-2 hover:underline">
              {title}
            </Link>
            {when ? <span>{when}</span> : null}
          </div>
          {s.text ? <p className="break-words text-sm text-foreground">{s.text}</p> : null}
          {s.follow_ups.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
              {s.follow_ups.map((f) => (
                <li key={f} className="break-words">
                  {f}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="flex items-center gap-1 sm:shrink-0">
          <Button
            size="sm"
            variant="ghost"
            icon="Trash2"
            aria-label={`Delete the summary of ${title}`}
            loading={busy}
            disabled={busy}
            onClick={() => v.onDeleteSummary(s.id)}
          >
            Delete
          </Button>
        </div>
      </div>
      <RowError text={v.errors[`summary-${s.id}`]} />
    </li>
  );
}

/**
 * "Recent chats" (PZ-35, `personalization.md` §15.5): the switch, and each
 * summary with Delete. It draws only when the organization has the flag on.
 */
function SummariesSection({ view, v }: { view: SummariesView; v: AssistantPaneViewProps }) {
  const list = view.list;
  const empty = list.status === "ready" && list.summaries.length === 0;
  return (
    <Section
      id="assistant-summaries"
      title="Recent chats"
      description="A short summary of each private chat, so the assistant can pick up where you left off. Other members never see it."
    >
      <label className="flex items-start gap-2.5">
        <Checkbox
          checked={view.keep}
          disabled={v.busy.summaries}
          onChange={(e) => v.onKeepSummaries(e.target.checked)}
          aria-label="Keep chat summaries"
        />
        <span className="space-y-0.5">
          <span className="block text-sm font-medium text-foreground">Keep chat summaries</span>
          <span className="block text-xs text-muted-foreground">
            When this is off, the assistant writes no new summary. The summaries below stay until
            you delete them.
          </span>
        </span>
      </label>
      <RowError text={v.errors.summaries} />
      {list.status === "loading" ? (
        <div data-testid="assistant-summaries-loading">
          <SkeletonRows count={2} />
        </div>
      ) : list.status === "error" ? (
        <div role="alert" className="space-y-2 rounded-lg border border-border bg-card p-3">
          <p className="text-xs text-foreground">The summaries did not load.</p>
          <p className="text-xs text-muted-foreground">{list.error || "Try again."}</p>
          <Button size="sm" variant="secondary" icon="RefreshCw" onClick={v.onRetrySummaries}>
            Retry
          </Button>
        </div>
      ) : empty ? (
        <p className="text-xs text-muted-foreground">
          No summaries yet. A summary comes after a private chat has been quiet for a while.
        </p>
      ) : (
        <>
          <ul className="space-y-2">
            {list.summaries.map((s) => (
              <SummaryRow key={s.id} s={s} v={v} />
            ))}
          </ul>
          <Button
            size="sm"
            variant="destructive"
            icon="Trash2"
            loading={v.busy["summaries-all"]}
            onClick={v.onDeleteAllSummaries}
          >
            Delete all
          </Button>
        </>
      )}
      <RowError text={v.errors["summaries-all"]} />
    </Section>
  );
}

function RowError({ text }: { text?: string }) {
  return text ? (
    <p role="alert" className="text-xs text-destructive">
      {text}
    </p>
  ) : null;
}

/** A counter shows only near its cap. Far from it, it is noise. */
function Counter({ used, cap, unit }: { used: number; cap: number; unit?: string }) {
  if (!nearCap(used, cap)) return null;
  return (
    <span className="text-[11px] text-warning">
      {used} of {cap}
      {unit ? ` ${unit}` : ""}
    </span>
  );
}

/** One single-choice field on the house dropdown. "" means no preference. */
function Choice({
  label,
  value,
  values,
  labels,
  none,
  width = "w-full",
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  values: readonly string[];
  labels?: Record<string, string>;
  none?: string;
  width?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  // A stored value that the list lacks (a tag set in chat) still shows.
  const all = value && !values.includes(value) ? [...values, value] : values;
  const options = [
    ...(none ? [{ value: "", label: none }] : []),
    ...all.map((x) => ({ value: x, label: labels?.[x] ?? x[0].toUpperCase() + x.slice(1) })),
  ];
  return (
    <SelectButton
      label={label}
      value={value}
      options={options}
      defaultValue={none ? "" : values[0]}
      widthClass={width}
      disabled={disabled}
      onChange={onChange}
    />
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}

/**
 * One draft field. It follows the server value while the member has not
 * changed it, and it always takes the server value after Forget everything
 * (`reconcileDraft`). `useState(server)` alone ran once, and left the old text
 * in the input with Save enabled.
 */
function useDraft<T extends string = string>(server: T, epoch: number): [T, (next: T) => void] {
  const [draft, setDraft] = useState<T>(server);
  const [seen, setSeen] = useState({ server: server as string, epoch });
  if (seen.server !== server || seen.epoch !== epoch) {
    setSeen({ server, epoch });
    const next = reconcileDraft(draft, seen, server, epoch);
    if (next !== null) setDraft(next as T);
  }
  return [draft, setDraft];
}

// ── Address ───────────────────────────────────────────────────────────────

function AddressSection({ p, v }: { p: Profile; v: AssistantPaneViewProps }) {
  const [name, setName] = useDraft(p.address.name ?? "", v.epoch ?? 0);
  const a: Address = p.address;
  const busy = v.busy.address;
  const fb = v.fallbackName;
  return (
    <Section
      id="assistant-address"
      title="How the assistant addresses you"
      description="The assistant uses these when it greets you and writes for you."
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Name">
          <div className="flex items-center gap-2">
            <Input
              inputSize="sm"
              value={name}
              maxLength={CAPS.name}
              placeholder={fb ? `Empty: ${fb.name}` : "What to call you"}
              onChange={(e) => setName(e.target.value)}
              className="w-full"
            />
            <Button
              size="sm"
              variant="secondary"
              loading={busy}
              disabled={busy || name.trim() === (a.name ?? "")}
              onClick={() => v.onAddress({ name })}
            >
              Save
            </Button>
          </div>
          <Counter used={name.length} cap={CAPS.name} unit="characters" />
        </Field>
        <Field label="Tone">
          <Choice
            label="Tone"
            value={a.formality ?? ""}
            values={FORMALITIES}
            none="No preference"
            disabled={busy}
            onChange={(x) => v.onAddress({ formality: (x || null) as Address["formality"] })}
          />
        </Field>
        <Field label="Greeting">
          <Choice
            label="Greeting"
            value={a.greeting ?? ""}
            values={GREETINGS}
            none="No preference"
            disabled={busy}
            onChange={(x) => v.onAddress({ greeting: (x || null) as Address["greeting"] })}
          />
        </Field>
      </div>
      {name.trim() === "" ? (
          <p className="text-[11px] text-muted-foreground">
            {fb
              ? `Empty: I call you ${fb.name}, ${
                  fb.from === "preferred" ? "your preferred name" : "the first word of your name"
                } in `
              : "Empty: I use your preferred name, or the first word of your name, from "}
            <Link href="/people/me" className="text-foreground underline underline-offset-2">
              My Profile
            </Link>
            .
          </p>
        ) : null}
      <RowError text={v.errors.address} />
    </Section>
  );
}

// ── Style ─────────────────────────────────────────────────────────────────

function StyleSection({ p, v }: { p: Profile; v: AssistantPaneViewProps }) {
  const s: Style = p.style;
  const notes = s.notes ?? [];
  const [note, setNote] = useDraft<string>("", v.epoch ?? 0);
  const busy = v.busy.style;
  return (
    <Section
      id="assistant-style"
      title="How the assistant answers"
      description="Length, languages and up to five notes on your style."
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Answer length">
          <Choice
            label="Answer length"
            value={s.length ?? ""}
            values={LENGTHS}
            none="No preference"
            disabled={busy}
            onChange={(x) => v.onStyle({ length: (x || null) as Style["length"] })}
          />
        </Field>
      </div>
      <LanguageList
        languages={styleLanguages(s)}
        busy={busy}
        onChange={(languages) => v.onStyle({ languages })}
      />
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-foreground">Notes</span>
          <Counter used={notes.length} cap={CAPS.notes} unit="notes" />
        </div>
        {notes.length === 0 ? (
          <p className="text-xs text-muted-foreground">No notes yet.</p>
        ) : (
          <ul className="space-y-1">
            {notes.map((n, i) => (
              <li
                key={`${i}-${n}`}
                className="flex items-center justify-between gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground"
              >
                <span className="min-w-0 break-words">{n}</span>
                <Button
                  size="icon"
                  variant="ghost"
                  icon="X"
                  aria-label="Remove note"
                  disabled={busy}
                  onClick={() => v.onStyle({ notes: notes.filter((_, j) => j !== i) })}
                />
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-2">
          <Input
            inputSize="sm"
            value={note}
            maxLength={CAPS.note}
            placeholder="For example: Give the answer first"
            onChange={(e) => setNote(e.target.value)}
            className="w-full"
            aria-label="New note"
          />
          <Button
            size="sm"
            variant="secondary"
            icon="Plus"
            className="shrink-0 whitespace-nowrap"
            disabled={busy || !note.trim() || notes.length >= CAPS.notes}
            onClick={async () => {
              if (await v.onStyle({ notes: [...notes, note.trim()] })) setNote("");
            }}
          >
            Add note
          </Button>
        </div>
      </div>
      <RowError text={v.errors.style} />
    </Section>
  );
}

// ── Languages I speak (PZ-53, `personalization.md` §5.3a) ─────────────────

/** The line under the list. The words are the owner's, and the spec quotes them. */
export const LANGUAGES_HELP =
  "The assistant understands any of these, and answers in the language you write or speak. Your records are always saved in English.";

/**
 * The ordered list of languages. Rank 1 is "Preferred". The member reorders
 * by drag or with the arrow buttons (both reach the keyboard), removes with
 * the cross, and adds from the menu of the languages not yet in the list.
 * Every change saves the whole list at once. An empty list stores nothing.
 */
function LanguageList({
  languages,
  busy,
  onChange,
}: {
  languages: string[];
  busy?: boolean;
  onChange: (languages: string[]) => Promise<boolean>;
}) {
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [said, setSaid] = useState("");
  const list = useRef<HTMLOListElement>(null);
  // A moved row is a moved DOM node, and a browser drops its focus. The
  // button that the member used takes it back once the new order draws.
  const refocus = useRef<{ tag: string; act: string } | null>(null);
  const order = languages.join(",");
  useEffect(() => {
    const want = refocus.current;
    refocus.current = null;
    if (!want || !list.current) return;
    const rows = list.current.querySelectorAll<HTMLButtonElement>(`[data-tag="${want.tag}"]`);
    const same = Array.from(rows).find((b) => b.dataset.act === want.act && !b.disabled);
    (same ?? Array.from(rows).find((b) => !b.disabled))?.focus();
  }, [order]);

  const move = async (from: number, to: number, act?: string) => {
    if (busy || to < 0 || to >= languages.length || from === to) return;
    const tag = languages[from];
    if (act) refocus.current = { tag, act };
    if (await onChange(moveLanguage(languages, from, to))) {
      setSaid(`${languageName(tag)} is now number ${to + 1}.`);
    }
  };
  const remove = async (i: number) => {
    if (busy) return;
    const name = languageName(languages[i]);
    if (await onChange(removeLanguage(languages, i))) setSaid(`${name} removed.`);
  };
  const full = languages.length >= CAPS.languages;
  const choices = languagesToAdd(languages);
  const menu = choices.map((t) => ({ value: t, label: `${LANGUAGES[t]} (${t})` }));

  return (
    <div className="space-y-2" data-testid="language-list">
      <div className="flex items-center justify-between">
        <span id="assistant-languages-label" className="text-xs font-medium text-foreground">
          Languages I speak
        </span>
        <Counter used={languages.length} cap={CAPS.languages} unit="languages" />
      </div>
      {languages.length === 0 ? (
        <div className="flex min-h-13 items-center gap-2 rounded-lg border border-dashed border-border px-3 py-1 text-xs text-muted-foreground">
          <strong className="font-medium text-foreground">English</strong>
          <span>The assistant follows the language of your message. Add a language to set an order.</span>
        </div>
      ) : (
        <ol ref={list} aria-labelledby="assistant-languages-label" className="space-y-1.5">
          {languages.map((tag, i) => (
            <li
              key={tag}
              draggable={!busy}
              onDragStart={(e) => {
                setDragFrom(i);
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", tag);
              }}
              onDragEnd={() => {
                setDragFrom(null);
                setOver(null);
              }}
              onDragOver={(e) => {
                if (dragFrom === null) return;
                e.preventDefault();
                setOver(i);
              }}
              onDragLeave={() => setOver((o) => (o === i ? null : o))}
              onDrop={(e) => {
                e.preventDefault();
                const from = dragFrom;
                setDragFrom(null);
                setOver(null);
                if (from !== null) void move(from, i);
              }}
              onKeyDown={(e) => {
                if (!e.altKey) return;
                if (e.key === "ArrowUp") {
                  e.preventDefault();
                  void move(i, i - 1);
                } else if (e.key === "ArrowDown") {
                  e.preventDefault();
                  void move(i, i + 1);
                }
              }}
              className={`flex min-h-13 items-center gap-2 rounded-lg border bg-background py-1 pl-2 pr-1 ${
                over === i && dragFrom !== null ? "border-primary ring-1 ring-primary" : "border-border"
              } ${dragFrom === i ? "opacity-50" : ""}`}
            >
              <span aria-hidden className="flex h-11 w-5 shrink-0 cursor-grab items-center justify-center text-muted-foreground">
                <Icon name="GripVertical" size={16} />
              </span>
              <span
                aria-hidden
                className={`flex h-6 min-w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums ${
                  i === 0 ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"
                }`}
              >
                {i + 1}
              </span>
              <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
                <span className="text-sm font-medium text-foreground">{languageName(tag)}</span>
                <span className="text-[11px] text-muted-foreground">{tag}</span>
                {i === 0 ? <span className="text-[11px] font-semibold text-primary">Preferred</span> : null}
              </span>
              <LanguageButton
                act="up"
                tag={tag}
                icon="ArrowUp"
                label={`Move ${languageName(tag)} up`}
                disabled={busy || i === 0}
                onClick={() => move(i, i - 1, "up")}
              />
              <LanguageButton
                act="down"
                tag={tag}
                icon="ArrowDown"
                label={`Move ${languageName(tag)} down`}
                disabled={busy || i === languages.length - 1}
                onClick={() => move(i, i + 1, "down")}
              />
              <LanguageButton
                act="remove"
                tag={tag}
                icon="X"
                label={`Remove ${languageName(tag)}`}
                disabled={busy}
                onClick={() => remove(i)}
              />
            </li>
          ))}
        </ol>
      )}
      <SelectButton
        label="Add a language"
        value=""
        options={menu}
        prompt={full ? `The list is full (${CAPS.languages} languages)` : "Add a language"}
        defaultValue=""
        widthClass="w-full"
        className="min-h-11 text-sm"
        disabled={busy || full || choices.length === 0}
        onChange={(x) => {
          if (!x) return;
          void onChange(addLanguage(languages, x)).then((ok) => {
            if (ok) setSaid(`${languageName(x)} added as number ${languages.length + 1}.`);
          });
        }}
      />
      <p className="text-xs text-muted-foreground">{LANGUAGES_HELP}</p>
      <p className="sr-only" role="status" aria-live="polite">
        {said}
      </p>
    </div>
  );
}

/** One 44 px icon button of a language row. */
function LanguageButton({
  act,
  tag,
  icon,
  label,
  disabled,
  onClick,
}: {
  act: string;
  tag: string;
  icon: string;
  label: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="none"
      icon={icon}
      aria-label={label}
      data-act={act}
      data-tag={tag}
      disabled={disabled}
      onClick={onClick}
      className="h-11 w-11 shrink-0 justify-center text-muted-foreground hover:text-foreground"
    />
  );
}

// ── Celebrations (PZ-43) ──────────────────────────────────────────────────

/** The words shown while the list of festivals is empty (`personalization.md` §16.9 Q3). */
export const FESTIVALS_LATER = "The list of festivals comes later. Until then, I greet no festival.";

/**
 * "Celebrations". The birthday is a day and a month, with no year field. The
 * festival picker draws only when the server lists a festival. The assistant
 * greets once on the day, and never guesses a festival from your name, your
 * region or your mail. Draws only with the flag on.
 */
function CelebrationsSection({
  p,
  gate,
  v,
}: {
  p: Profile;
  gate: CelebrationsGate;
  v: AssistantPaneViewProps;
}) {
  const c = p.celebrations;
  const [month, setMonth] = useDraft<string>(c?.birthday ? String(c.birthday.month) : "", v.epoch ?? 0);
  const [day, setDay] = useDraft<string>(c?.birthday ? String(c.birthday.day) : "", v.epoch ?? 0);
  const busy = v.busy.celebrations;
  const stored = c?.birthday ? `${c.birthday.month}-${c.birthday.day}` : "";
  const draft = month && day ? `${month}-${day}` : "";
  // Save when the draft is a whole date, or when both parts are cleared.
  const whole = (month === "") === (day === "");
  const canSave = !busy && whole && draft !== stored;
  const max = month ? daysInMonth(Number(month)) : 31;
  const days = Array.from({ length: max }, (_, i) => String(i + 1));
  const monthLabels = Object.fromEntries(MONTHS.map((m, i) => [String(i + 1), m]));
  const picked = c?.festivals ?? [];
  return (
    <Section
      id="assistant-celebrations"
      title="Celebrations"
      description="Tell the assistant which days to greet you on. It greets once on the day. It never guesses, and other members never see it."
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Birthday month">
          <Choice
            label="Birthday month"
            value={month}
            values={MONTHS.map((_, i) => String(i + 1))}
            labels={monthLabels}
            none="Not set"
            disabled={busy}
            onChange={(x) => {
              setMonth(x);
              if (x && day && Number(day) > daysInMonth(Number(x))) setDay("");
            }}
          />
        </Field>
        <Field label="Birthday day">
          <Choice
            label="Birthday day"
            value={day}
            values={days}
            labels={Object.fromEntries(days.map((d) => [d, d]))}
            none="Not set"
            disabled={busy}
            onChange={setDay}
          />
        </Field>
        <div className="flex items-end">
          <Button
            size="sm"
            variant="secondary"
            loading={busy}
            disabled={!canSave}
            onClick={() =>
              v.onCelebrations({
                birthday: draft ? { month: Number(month), day: Number(day) } : null,
              })
            }
          >
            Save birthday
          </Button>
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">
        A day and a month only. I do not keep a year.
      </p>
      {gate.festivals.length === 0 ? (
        <p data-testid="celebrations-festivals-later" className="text-xs text-muted-foreground">
          {FESTIVALS_LATER}
        </p>
      ) : (
        <fieldset className="space-y-2" data-testid="celebrations-festivals">
          <legend className="text-xs font-medium text-foreground">Festivals to greet me on</legend>
          {gate.festivals.map((f) => (
            <label key={f.id} className="flex items-center gap-2.5">
              <Checkbox
                checked={picked.includes(f.id)}
                disabled={busy}
                onChange={(e) =>
                  v.onCelebrations({
                    festivals: e.target.checked ? [...picked, f.id] : picked.filter((x) => x !== f.id),
                  })
                }
                aria-label={f.name}
              />
              <span className="text-sm text-foreground">{f.name}</span>
            </label>
          ))}
        </fieldset>
      )}
      <label className="flex items-start gap-2.5">
        <Checkbox
          checked={c?.work_anniversary === true}
          disabled={busy}
          onChange={(e) => v.onCelebrations({ work_anniversary: e.target.checked })}
          aria-label="Greet me on my work anniversary"
        />
        <span className="space-y-0.5">
          <span className="block text-sm font-medium text-foreground">
            Greet me on my work anniversary
          </span>
          <span className="block text-xs text-muted-foreground">
            It uses the join date in your People profile. It is off until you turn it on.
          </span>
        </span>
      </label>
      <RowError text={v.errors.celebrations} />
    </Section>
  );
}

// ── Facts ─────────────────────────────────────────────────────────────────

function FactRow({ f, v }: { f: Fact; v: AssistantPaneViewProps }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useDraft(f.text, v.epoch ?? 0);
  const busy = v.busy[f.id];
  const when = shortDate(f.at);
  const src = sourceLabel(f.source);
  return (
    <li className="space-y-1.5 rounded-lg border border-border bg-card px-3 py-2">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1 space-y-1">
          {editing ? (
            <div className="space-y-2">
              <Textarea
                inputSize="sm"
                rows={3}
                value={text}
                maxLength={CAPS.fact}
                onChange={(e) => setText(e.target.value)}
                className="w-full"
                aria-label="Fact text"
              />
              <div className="flex items-center gap-2">
              <Counter used={text.length} cap={CAPS.fact} unit="characters" />
              <Button
                size="sm"
                loading={busy}
                disabled={busy || !text.trim() || text.trim() === f.text}
                onClick={async () => {
                  if (await v.onEditFact(f.id, { text: text.trim() })) setEditing(false);
                }}
              >
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setText(f.text);
                  setEditing(false);
                }}
              >
                Cancel
              </Button>
              </div>
            </div>
          ) : (
            <p className="break-words text-sm text-foreground">{f.text}</p>
          )}
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            {areaLabel(f.area) ? <Badge size="xs">{areaLabel(f.area)}</Badge> : null}
            {src ? <span>{src}</span> : null}
            {when ? <span>{when}</span> : null}
          </div>
        </div>
        {editing ? null : (
          <div className="flex items-center gap-1 sm:shrink-0">
            <Choice
              label="Fact kind"
              value={f.kind}
              values={FACT_KINDS}
              labels={KIND_LABELS}
              width="w-36"
              disabled={busy}
              onChange={(x) => v.onEditFact(f.id, { kind: x as FactKind })}
            />
            <Button size="icon" variant="ghost" icon="Pencil" aria-label="Edit fact" disabled={busy} onClick={() => setEditing(true)} />
            <Button size="icon" variant="ghost" icon="Trash2" aria-label="Delete fact" disabled={busy} onClick={() => v.onDeleteFact(f.id)} />
          </div>
        )}
      </div>
      <RowError text={v.errors[f.id]} />
    </li>
  );
}

function FactsSection({ p, v }: { p: Profile; v: AssistantPaneViewProps }) {
  const [text, setText] = useDraft<string>("", v.epoch ?? 0);
  const [kind, setKind] = useDraft<FactKind>("about", v.epoch ?? 0);
  const full = p.facts.length >= CAPS.facts;
  const busy = v.busy["new-fact"];
  return (
    <Section
      id="assistant-facts"
      title="What the assistant remembers"
      description="Each fact shows where it came from and when. Edit or delete any of them."
    >
      {nearCap(p.facts.length, CAPS.facts) ? (
        <div className="flex justify-end">
          <Counter used={p.facts.length} cap={CAPS.facts} unit="facts" />
        </div>
      ) : null}
      {p.facts.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Nothing yet. Tell the assistant &quot;remember that…&quot; in chat, or add a fact here.
        </p>
      ) : (
        <ul className="space-y-2">
          {p.facts.map((f) => (
            <FactRow key={f.id} f={f} v={v} />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          inputSize="sm"
          value={text}
          maxLength={CAPS.fact}
          placeholder="Add a fact"
          aria-label="New fact"
          onChange={(e) => setText(e.target.value)}
          className="min-w-0 flex-1"
        />
        <Choice
          label="New fact kind"
          value={kind}
          values={FACT_KINDS}
          labels={KIND_LABELS}
          width="w-36"
          onChange={(x) => setKind(x as FactKind)}
        />
        <Button
          size="sm"
          variant="secondary"
          icon="Plus"
          className="shrink-0 whitespace-nowrap"
          loading={busy}
          disabled={busy || full || !text.trim()}
          onClick={async () => {
            if (await v.onAddFact(text.trim(), kind)) setText("");
          }}
        >
          Add fact
        </Button>
      </div>
      {full ? <p className="text-xs text-muted-foreground">You have {CAPS.facts} facts. Delete one to add another.</p> : null}
      <RowError text={v.errors["new-fact"]} />
    </Section>
  );
}

// ── The interview (PZ-27) ─────────────────────────────────────────────────

const STATE_TONES: Record<InterviewState | "open", BadgeTone> = {
  open: "neutral",
  asked: "info",
  answered: "success",
  skipped: "warning",
  never: "neutral",
};

function InterviewSection({ p, v }: { p: Profile; v: AssistantPaneViewProps }) {
  const areas = p.interview?.areas ?? {};
  return (
    <Section
      id="assistant-interview"
      title="Get to know me"
      description="The assistant asks one question a day, and never twice about an area you answered. Each answer becomes a fact above. Resetting keeps the facts."
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" icon="ClipboardList" onClick={v.onGetToKnow}>
          Get to know me
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon="RotateCcw"
          loading={v.busy["interview-reset"]}
          disabled={!p.interview}
          onClick={v.onResetInterview}
        >
          Reset the interview
        </Button>
      </div>
      <ul className="space-y-1" aria-label="Interview areas">
        {INTERVIEW_AREAS.map((a) => {
          const state = areas[a.id]?.state;
          const key = state ?? "open";
          const busy = v.busy[`area-${a.id}`];
          return (
            <li
              key={a.id}
              className="flex flex-col gap-1.5 rounded-lg border border-border bg-card px-3 py-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3"
            >
              <span className="min-w-0">
                <span className="block text-sm text-foreground">{a.label}</span>
                <span className="block text-xs text-muted-foreground">{a.question}</span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <Badge tone={STATE_TONES[key]}>{STATE_LABELS[key]}</Badge>
                {state ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    icon="RotateCcw"
                    loading={busy}
                    disabled={busy}
                    aria-label={`Reset ${a.label}`}
                    onClick={() => v.onResetArea(a.id)}
                  >
                    Reset
                  </Button>
                ) : null}
              </span>
              <RowError text={v.errors[`area-${a.id}`]} />
            </li>
          );
        })}
      </ul>
      <RowError text={v.errors["interview-reset"]} />
    </Section>
  );
}

// ── Aliases ───────────────────────────────────────────────────────────────

function AliasRow({ a, v }: { a: Alias; v: AssistantPaneViewProps }) {
  const [editing, setEditing] = useState(false);
  const [alias, setAlias] = useDraft(a.alias, v.epoch ?? 0);
  const [email, setEmail] = useDraft(a.email, v.epoch ?? 0);
  const busy = v.busy[a.id];
  const changed = alias.trim() !== a.alias || email.trim() !== a.email;
  return (
    <li className="space-y-1.5 rounded-lg border border-border bg-card px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {editing ? (
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <Input inputSize="sm" value={alias} maxLength={CAPS.alias} onChange={(e) => setAlias(e.target.value)} aria-label="Name" className="w-40" />
            <Input inputSize="sm" value={email} maxLength={CAPS.email} onChange={(e) => setEmail(e.target.value)} aria-label="Email" className="min-w-0 flex-1" />
            <Button
              size="sm"
              loading={busy}
              disabled={busy || !alias.trim() || !email.trim() || !changed}
              onClick={async () => {
                // One PATCH changes one field. Send the changed one(s) in turn.
                let ok = true;
                if (alias.trim() !== a.alias) ok = await v.onEditAlias(a.id, { alias: alias.trim() });
                if (ok && email.trim() !== a.email) ok = await v.onEditAlias(a.id, { email: email.trim() });
                if (ok) setEditing(false);
              }}
            >
              Save
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setAlias(a.alias); setEmail(a.email); setEditing(false); }}>
              Cancel
            </Button>
          </div>
        ) : (
          <>
            <p className="min-w-0 break-words text-sm text-foreground">
              <span className="font-medium">{a.alias}</span>
              <span className="text-muted-foreground"> is {a.email}</span>
            </p>
            <div className="flex items-center gap-1">
              <Button size="icon" variant="ghost" icon="Pencil" aria-label="Edit name" disabled={busy} onClick={() => setEditing(true)} />
              <Button size="icon" variant="ghost" icon="Trash2" aria-label="Delete name" disabled={busy} onClick={() => v.onDeleteAlias(a.id)} />
            </div>
          </>
        )}
      </div>
      <RowError text={v.errors[a.id]} />
    </li>
  );
}

function AliasesSection({ p, v }: { p: Profile; v: AssistantPaneViewProps }) {
  const [alias, setAlias] = useDraft<string>("", v.epoch ?? 0);
  const [email, setEmail] = useDraft<string>("", v.epoch ?? 0);
  const full = p.aliases.length >= CAPS.aliases;
  const busy = v.busy["new-alias"];
  return (
    <Section
      id="assistant-names"
      title="Names for people"
      description="Say &quot;Mum&quot; or &quot;my accountant&quot; and the assistant knows whom you mean."
    >
      {nearCap(p.aliases.length, CAPS.aliases) ? (
        <div className="flex justify-end">
          <Counter used={p.aliases.length} cap={CAPS.aliases} unit="names" />
        </div>
      ) : null}
      {p.aliases.length === 0 ? (
        <p className="text-xs text-muted-foreground">No names yet.</p>
      ) : (
        <ul className="space-y-2">
          {p.aliases.map((a) => (
            <AliasRow key={a.id} a={a} v={v} />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input inputSize="sm" value={alias} maxLength={CAPS.alias} placeholder="Name, for example Mum" aria-label="New name" onChange={(e) => setAlias(e.target.value)} className="w-44" />
        <Input inputSize="sm" value={email} maxLength={CAPS.email} placeholder="Email address" aria-label="New email" onChange={(e) => setEmail(e.target.value)} className="min-w-0 flex-1" />
        <Button
          size="sm"
          variant="secondary"
          icon="Plus"
          className="shrink-0 whitespace-nowrap"
          loading={busy}
          disabled={busy || full || !alias.trim() || !email.trim()}
          onClick={async () => {
            if (await v.onAddAlias(alias.trim(), email.trim())) {
              setAlias("");
              setEmail("");
            }
          }}
        >
          Add name
        </Button>
      </div>
      {full ? <p className="text-xs text-muted-foreground">You have {CAPS.aliases} names. Delete one to add another.</p> : null}
      <RowError text={v.errors["new-alias"]} />
    </Section>
  );
}

// ── The view ──────────────────────────────────────────────────────────────

export function AssistantPaneView(v: AssistantPaneViewProps) {
  if (v.status === "loading") {
    return (
      <div className="space-y-4" data-testid="assistant-loading">
        <Skeleton className="h-6 w-1/3" />
        <SkeletonRows count={5} />
      </div>
    );
  }
  if (v.status === "error") {
    return (
      <div role="alert" className="space-y-3 rounded-lg border border-border bg-card p-4">
        <p className="text-sm text-foreground">The assistant profile did not load.</p>
        <p className="text-xs text-muted-foreground">{v.loadError || "Try again."}</p>
        <Button size="sm" variant="secondary" icon="RefreshCw" onClick={v.onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  const p = v.profile;
  return (
    <div className="space-y-8">
      <nav aria-label="Sections on this page" className="flex flex-wrap gap-1.5">
        {jumpsFor(v).map((j) => (
          <Button
            key={j.id}
            size="sm"
            variant="ghost"
            onClick={() =>
              document.getElementById(j.id)?.scrollIntoView({ behavior: "smooth", block: "start" })
            }
          >
            {j.label}
          </Button>
        ))}
      </nav>
      <Section id="assistant-memory" title="Memory">
        <p className="text-xs text-muted-foreground">
          The assistant reads this before each of your chats, on the web and on WhatsApp. Other
          members never see it. Learning is {p.learning ? "on" : "off"}.
        </p>
        <label className="flex items-start gap-2.5">
          <Checkbox
            checked={p.learning}
            disabled={v.busy.learning}
            onChange={(e) => v.onLearning(e.target.checked)}
            aria-label="Let the assistant remember what I tell it"
          />
          <span className="space-y-0.5">
            <span className="block text-sm font-medium text-foreground">
              Let the assistant remember what I tell it
            </span>
            <span className="block text-xs text-muted-foreground">
              When this is off, the assistant stops writing to this list. It still reads what is
              here, and you can still edit or delete it.
            </span>
          </span>
        </label>
        <RowError text={v.errors.learning} />
      </Section>
      <AddressSection p={p} v={v} />
      <StyleSection p={p} v={v} />
      {v.celebrations ? <CelebrationsSection p={p} gate={v.celebrations} v={v} /> : null}
      <FactsSection p={p} v={v} />
      <InterviewSection p={p} v={v} />
      <AliasesSection p={p} v={v} />
      {v.block ? <BlockSection block={v.block} onRetry={v.onRetryBlock} /> : null}
      {v.summaries ? <SummariesSection view={v.summaries} v={v} /> : null}
      <Section
        id="assistant-download"
        title="Download my profile"
        description="A copy of everything on this page, for you to keep. It holds nothing about anybody else."
      >
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            icon="Download"
            loading={v.busy["export-json"]}
            onClick={() => v.onExport("json")}
          >
            Download as JSON
          </Button>
          <Button
            variant="secondary"
            size="sm"
            icon="Download"
            loading={v.busy["export-text"]}
            onClick={() => v.onExport("text")}
          >
            Download as text
          </Button>
        </div>
        <RowError text={v.errors["export-json"] || v.errors["export-text"]} />
      </Section>
      <Section
        id="assistant-forget"
        title="Forget everything"
        description="Clears every fact, name and style note above, and every chat summary, at once. The Keep chat summaries switch stays as it is. You cannot undo this."
      >
        <Button variant="destructive" size="sm" icon="Trash2" loading={v.busy.forget} onClick={v.onForget}>
          Forget everything
        </Button>
        <RowError text={v.errors.forget} />
      </Section>
      <ConfirmDialog
        open={v.confirmForget}
        title="Forget everything?"
        body="The assistant loses every fact, name and style note you set, and every chat summary. The Keep chat summaries switch stays as it is. It cannot be undone."
        confirmLabel="Forget everything"
        confirmVariant="destructive"
        busy={v.busy.forget}
        onConfirm={v.onConfirmForget}
        onCancel={v.onCancelForget}
      />
      {v.summaries && v.summaries.list.status === "ready" ? (
        <ConfirmDialog
          open={!!v.confirmDeleteSummaries}
          title="Delete all chat summaries?"
          body={deleteAllBody(v.summaries.list)}
          confirmLabel="Delete all"
          confirmVariant="destructive"
          busy={v.busy["summaries-all"]}
          onConfirm={v.onConfirmDeleteSummaries ?? (() => {})}
          onCancel={v.onCancelDeleteSummaries ?? (() => {})}
        />
      ) : null}
      {v.summaries ? (
        <ConfirmDialog
          open={!!v.confirmDeleteSummaryId}
          title="Delete this summary?"
          body={DELETE_ONE_BODY}
          confirmLabel="Delete"
          confirmVariant="destructive"
          busy={v.confirmDeleteSummaryId ? v.busy[`summary-${v.confirmDeleteSummaryId}`] : false}
          onConfirm={v.onConfirmDeleteSummary ?? (() => {})}
          onCancel={v.onCancelDeleteSummary ?? (() => {})}
        />
      ) : null}
      <ConfirmDialog
        open={v.confirmResetInterview}
        title="Reset the interview?"
        body="Every area becomes open again, and the assistant may offer the questions again. The facts you gave stay."
        confirmLabel="Reset the interview"
        confirmVariant="primary"
        icon="RotateCcw"
        busy={v.busy["interview-reset"]}
        onConfirm={v.onConfirmResetInterview}
        onCancel={v.onCancelResetInterview}
      />
    </div>
  );
}

// ── The container ─────────────────────────────────────────────────────────

export default function AssistantPane() {
  const toast = useToast();
  const [status, setStatus] = useState<"loading" | "error" | "ready">("loading");
  const [loadError, setLoadError] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirmForget, setConfirmForget] = useState(false);
  const [confirmResetInterview, setConfirmResetInterview] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const [fallbackName, setFallbackName] = useState<AssistantPaneViewProps["fallbackName"]>(null);
  const [block, setBlock] = useState<BlockState>({ status: "loading" });
  const [blockTry, setBlockTry] = useState(0);
  // Recent chats (PZ-35). The gate is what the profile read says, and the list
  // loads only when the organization has the flag on.
  const [gate, setGate] = useState<SummaryGate>({ enabled: false, keep: true });
  // Celebrations (PZ-43): the flag of the organization and the festival list.
  const [celeb, setCeleb] = useState<CelebrationsGate>(celebrationsGate(null));
  const [summaries, setSummaries] = useState<SummariesList>({ status: "loading" });
  const [summariesTry, setSummariesTry] = useState(0);
  const [confirmDeleteSummaries, setConfirmDeleteSummaries] = useState(false);
  const [confirmDeleteSummaryId, setConfirmDeleteSummaryId] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "ready" || !gate.enabled) return;
    let live = true;
    fetchSummaries()
      .then((page) => live && setSummaries({ status: "ready", ...page }))
      .catch((e) =>
        live &&
        setSummaries({ status: "error", error: e instanceof ProfileError ? e.message : errorText("", 502) }),
      );
    return () => {
      live = false;
    };
  }, [status, gate.enabled, summariesTry]);

  // The text the assistant reads (PZ-20a). It loads again after each change
  // of the profile, so the member sees what a save did. The old text stays
  // on screen while the new text loads.
  useEffect(() => {
    if (status !== "ready") return;
    let live = true;
    fetchBlock()
      .then((b) => live && setBlock({ status: "ready", ...b }))
      .catch((e) =>
        live &&
        setBlock({ status: "error", error: e instanceof ProfileError ? e.message : errorText("", 502) }),
      );
    return () => {
      live = false;
    };
  }, [status, profile, blockTry]);

  // The name the assistant uses while the Name field is empty. A failed read
  // leaves the plain rule, with no value.
  useEffect(() => {
    let live = true;
    peopleApi
      .me()
      .then((res) => {
        if (live && res.state === "resolved" && res.person) {
          setFallbackName(fallbackAddress(res.person.preferred_name, res.person.name));
        }
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const load = useCallback(async () => {
    setStatus("loading");
    try {
      const read = await fetchProfileAndGate();
      setProfile(read.profile);
      setGate(read.gate);
      setCeleb(read.celebrations);
      setStatus("ready");
    } catch (e) {
      setLoadError(e instanceof ProfileError ? e.message : errorText("", 502));
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Run one call under a busy and error key. True when it worked. */
  const run = useCallback(async (key: string, work: () => Promise<void>): Promise<boolean> => {
    setBusy((b) => ({ ...b, [key]: true }));
    setErrors((er) => {
      const { [key]: _gone, ...rest } = er;
      return rest;
    });
    try {
      await work();
      return true;
    } catch (e) {
      const text = e instanceof ProfileError ? e.message : errorText("", 502);
      setErrors((er) => ({ ...er, [key]: text }));
      return false;
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
    }
  }, []);

  const cur = profile;
  if (status === "ready" && !cur) return null;
  const base: Profile = cur ?? { v: 1, learning: true, address: {}, style: {}, aliases: [], facts: [] };

  const handlers: PaneHandlers = {
    onLearning: (value) => run("learning", async () => setProfile(await saveLearning(value))),
    onAddress: (edit) =>
      run("address", async () => setProfile(await saveAddress(mergeAddress(base.address, edit)))),
    onStyle: (edit) =>
      run("style", async () => setProfile(await saveStyle(mergeStyle(base.style, edit)))),
    onAddFact: (text, kind, area) =>
      run("new-fact", async () => {
        const f = await addFact(text, kind, area);
        if (f) setProfile((p) => (p ? { ...p, facts: [...p.facts, f] } : p));
      }),
    onEditFact: (id, patch) =>
      run(id, async () => {
        const f = await patchFact(id, patch);
        if (f) setProfile((p) => (p ? { ...p, facts: p.facts.map((x) => (x.id === id ? f : x)) } : p));
      }),
    onDeleteFact: async (id) => {
      const gone = base.facts.find((x) => x.id === id);
      const ok = await run(id, async () => {
        await deleteFact(id);
        setProfile((p) => (p ? { ...p, facts: p.facts.filter((x) => x.id !== id) } : p));
      });
      if (ok && gone) {
        toast.show({
          variant: "success",
          title: "Fact deleted",
          description: gone.text.length > 80 ? `${gone.text.slice(0, 80)}…` : gone.text,
          action: {
            label: "Undo",
            onClick: () => void handlers.onAddFact(gone.text, gone.kind, gone.area),
          },
          timeout: 8000,
        });
      }
      return ok;
    },
    onAddAlias: (alias, email) =>
      run("new-alias", async () => {
        const a = await addAlias(alias, email);
        if (a) setProfile((p) => (p ? { ...p, aliases: [...p.aliases, a] } : p));
      }),
    onEditAlias: (id, patch) =>
      run(id, async () => {
        const a = await patchAlias(id, patch);
        if (a) setProfile((p) => (p ? { ...p, aliases: p.aliases.map((x) => (x.id === id ? a : x)) } : p));
      }),
    onDeleteAlias: async (id) => {
      const gone = base.aliases.find((x) => x.id === id);
      const ok = await run(id, async () => {
        await deleteAlias(id);
        setProfile((p) => (p ? { ...p, aliases: p.aliases.filter((x) => x.id !== id) } : p));
      });
      if (ok && gone) {
        toast.show({
          variant: "success",
          title: "Name deleted",
          description: `${gone.alias} is ${gone.email}`,
          action: {
            label: "Undo",
            onClick: () => void handlers.onAddAlias(gone.alias, gone.email),
          },
          timeout: 8000,
        });
      }
      return ok;
    },
    onForget: () => setConfirmForget(true),
    onGetToKnow: startGetToKnowMe,
    onResetArea: (id) =>
      run(`area-${id}`, async () => {
        await resetArea(id);
        setProfile((p) => (p ? withoutArea(p, id) : p));
      }),
    onResetInterview: () => setConfirmResetInterview(true),
    onCelebrations: (edit) =>
      run("celebrations", async () =>
        setProfile(await saveCelebrations(mergeCelebrations(base.celebrations, edit))),
      ),
    onKeepSummaries: (value) =>
      run("summaries", async () => {
        await saveKeepSummaries(value);
        setGate((g) => ({ ...g, keep: value }));
      }),
    onDeleteSummary: (id) => setConfirmDeleteSummaryId(id),
    onDeleteAllSummaries: () => setConfirmDeleteSummaries(true),
    onRetrySummaries: () => {
      setSummaries({ status: "loading" });
      setSummariesTry((n) => n + 1);
    },
    onRetry: () => void load(),
    onRetryBlock: () => {
      setBlock({ status: "loading" });
      setBlockTry((n) => n + 1);
    },
    onExport: (format) =>
      run(`export-${format}`, async () => {
        const file = await fetchExport(format);
        saveBlob(file.body, file.filename);
      }),
  };

  return (
    <AssistantPaneView
      {...handlers}
      status={status}
      loadError={loadError}
      profile={base}
      busy={busy}
      errors={errors}
      epoch={epoch}
      fallbackName={fallbackName}
      block={block}
      celebrations={celeb.enabled ? celeb : undefined}
      summaries={gate.enabled ? { keep: gate.keep, list: summaries } : undefined}
      confirmDeleteSummaryId={confirmDeleteSummaryId}
      onCancelDeleteSummary={() => setConfirmDeleteSummaryId(null)}
      onConfirmDeleteSummary={async () => {
        const id = confirmDeleteSummaryId;
        if (!id) return;
        const ok = await run(`summary-${id}`, async () => {
          await deleteSummary(id);
          setSummaries((l) =>
            l.status === "ready" ? { ...l, summaries: l.summaries.filter((x) => x.id !== id) } : l,
          );
        });
        setConfirmDeleteSummaryId(null);
        if (ok) toast.show({ variant: "success", title: "The summary is deleted" });
      }}
      confirmDeleteSummaries={confirmDeleteSummaries}
      onCancelDeleteSummaries={() => setConfirmDeleteSummaries(false)}
      onConfirmDeleteSummaries={async () => {
        const ok = await run("summaries-all", async () => {
          await deleteAllSummaries();
          setSummaries((l) => (l.status === "ready" ? { status: "ready", summaries: [], more: false } : l));
        });
        setConfirmDeleteSummaries(false);
        if (ok) toast.show({ variant: "success", title: "The chat summaries are deleted" });
      }}
      confirmForget={confirmForget}
      onCancelForget={() => setConfirmForget(false)}
      confirmResetInterview={confirmResetInterview}
      onCancelResetInterview={() => setConfirmResetInterview(false)}
      onConfirmResetInterview={async () => {
        const ok = await run("interview-reset", async () => {
          await resetInterview();
          setProfile((p) => (p ? withoutInterview(p) : p));
        });
        setConfirmResetInterview(false);
        if (ok) toast.show({ variant: "success", title: "The interview is reset" });
      }}
      onConfirmForget={async () => {
        const ok = await run("forget", async () => {
          setProfile(await forgetAll());
          setEpoch((n) => n + 1);
          // Forget clears the summaries and keeps the switch (PZ-35).
          setSummariesTry((n) => n + 1);
        });
        setConfirmForget(false);
        if (ok) toast.show({ variant: "success", title: "The assistant forgot everything" });
      }}
    />
  );
}
