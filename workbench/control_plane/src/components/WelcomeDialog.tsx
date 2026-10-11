"use client";

/**
 * WelcomeDialog — the first-run moment, and the first sign-in question.
 *
 * Three steps, in ONE dialog, so a member never sees two in a row:
 *
 * 1. **The question** (NS-7, `navigation_shell.md` §8.4). "What will you do
 *    most here?", with six answers and "Skip for now". The answer picks a
 *    preset (`presets.ts`), which orders My Day's cards and the command
 *    bar's jobs. Every member is asked on their first visit, invited members
 *    too: the trigger is a layout the server holds as "never asked"
 *    (`answered` null). "Skip for now" is stored as a choice, so nobody is
 *    asked twice, and it stars nothing away: every app shows. "Change my
 *    layout" in the account menu asks again (`CHANGE_LAYOUT`).
 * 2. **The apps** (owner, 2026-10-11). After an answer, "Choose the apps for
 *    your sidebar" lists every app the member holds, by category, with a
 *    tick on each app that shows. "Done" saves the answer and the stars in
 *    ONE write. On a first visit, the X or Escape saves the answer with the
 *    ticks it started with, so an answer is never lost. Asked again from
 *    the menu, the X or Escape closes and writes NOTHING, as "Keep my
 *    layout" does. "Back" goes to the question.
 *    - A first answer ticks every Personal Center app and the preset's apps
 *      (`answerStars`, "Personal + preset").
 *    - Asked again, the same answer ticks the member's CURRENT stars. A
 *      different answer ticks that answer's defaults instead.
 * 3. **The founder's welcome** (CP-2c, owner directive 2026-08-24). Armed by
 *    `?welcome=new-org`, which `SignUpForm` appends to its post-create
 *    redirect. It says "your organization is ready" and "here is where you
 *    add your team". With the question on, it is the step after the apps.
 *
 * ⚠️ A read that fails asks nothing. "Never asked" is a fact only the server
 * knows, and asking on a fault would ask a member who already answered.
 *
 * ⚠️ A WRITE that fails never holds the member here (round 2). Every way out
 * closes first, the choice holds for this page, and one toast says the save
 * did not land. A later visit asks again, because the server still says
 * "never asked".
 *
 * With the shell nav off (`shellNavOn`), only the founder's welcome exists,
 * exactly as before NS-7.
 *
 * Dismissing the welcome strips the query with `router.replace`, so a reload,
 * a share of the URL or the back button cannot summon it again (no
 * localStorage: a second founder on a shared machine still gets their own).
 *
 * Renders through the ONE Modal primitive (`components/ui/Modal`).
 * `useSearchParams` requires a Suspense boundary at build. The default export
 * carries it so AppShell can mount this bare.
 */

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { visibleSections, type NavSection } from "@/lib/nav";
import {
  ANSWERS,
  EMPTY_SHELL,
  QUESTION,
  answerStars,
  canStar,
  presetById,
  presetExtras,
  starredHrefs,
  starrableHeld,
  starsToSave,
  type PresetId,
} from "@/lib/shell/presets";
import { launcherGroups, shellNavOn } from "@/lib/shell/shellNav";
import { layoutQuestionOpen } from "@/lib/shell/welcomeSteps";
import { CHANGE_LAYOUT, askedInThisPage, markAsked, saveShellPrefs, useShellPrefs } from "@/lib/shell/shellPrefs";

export const WELCOME_PARAM = "welcome";
export const WELCOME_NEW_ORG = "new-org";

/** The title of step 2. */
export const APPS_STEP = "Choose the apps for your sidebar";

/** "Projects, Approvals and Chat". */
function listWords(words: readonly string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** Step 2's list: every app the member may star, by category. */
export function appChoices(sections: readonly NavSection[]): NavSection[] {
  return launcherGroups(sections)
    .map((g) => ({ ...g, items: g.items.filter(canStar) }))
    .filter((g) => g.items.length > 0);
}

function InviteBody({ onDone }: { onDone: () => void }) {
  return (
    <div className="flex flex-col gap-4 p-4">
      <p className="text-sm text-muted-foreground">
        Working with a team? Invite them from{" "}
        <span className="font-medium text-foreground">Settings → Organization</span>{" "}
        — each teammate gets an email, signs in with their own address, and lands
        straight in your workspace.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button variant="secondary" onClick={onDone}>
          Explore on my own first
        </Button>
        <Link href="/settings/organization" onClick={onDone}>
          <Button className="w-full">Invite my team</Button>
        </Link>
      </div>
    </div>
  );
}

function WelcomeDialogInner() {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const params = useSearchParams();
  const newOrg = params?.get(WELCOME_PARAM) === WELCOME_NEW_ORG;
  // Read once, as the sidebar reads it: the dev override must not flip.
  const [navOn] = useState(() => shellNavOn());
  const shell = useShellPrefs();
  const { access, loading } = useAccess();
  const sections = visibleSections(loading ? null : access.features, access.is_admin);

  // ── When the question shows ─────────────────────────────────────────────
  // Only on the SERVER's "never asked" (`fresh`), never on the browser copy,
  // which may be out of date. `asking` latches: an answer shows at once
  // (`saveShellPrefs` is optimistic), and the live value then reads
  // "answered" while the founder's welcome still has a step to show. The
  // page latch (`askedInThisPage`) stops a remount from asking twice.
  const neverAsked = navOn && shell.enabled && shell.fresh !== undefined && shell.fresh.answered === null;
  const [asking, setAsking] = useState(false);
  const [reopened, setReopened] = useState(false);
  const [handled, setHandled] = useState(false);
  if (neverAsked && !asking && !handled && !askedInThisPage()) setAsking(true);
  useEffect(() => {
    if (asking) markAsked();
  }, [asking]);
  const [step, setStep] = useState<"ask" | "apps" | "invite">("ask");
  // Step 2: the answer, the ticks it started with, and the ticks now.
  const [chosen, setChosen] = useState<PresetId | null>(null);
  const [defaults, setDefaults] = useState<readonly string[]>([]);
  const [ticks, setTicks] = useState<ReadonlySet<string>>(new Set());
  // The answer to focus when step 1 shows again ("Back").
  const [refocus, setRefocus] = useState<PresetId | null>(null);
  const toast = useToast();

  // "Change my layout" in the account menu asks again.
  useEffect(() => {
    const onChange = () => {
      setStep("ask");
      setReopened(true);
    };
    window.addEventListener(CHANGE_LAYOUT, onChange);
    return () => window.removeEventListener(CHANGE_LAYOUT, onChange);
  }, []);

  // ⚠️ Only once access has resolved. Before that `sections` is empty, so
  // step 2 would list no app, and "Done" would save `pins: []`.
  const questionOpen = layoutQuestionOpen({ navOn, asking, reopened, step, accessLoading: loading });
  // The founder's welcome: after the question, or alone when there is none.
  // It waits for the layout read, or it would show first and then give way
  // to the question it should follow.
  const inviteOpen =
    newOrg && !questionOpen && !(navOn && shell.loading) && (step === "invite" || !(asking || reopened));

  // "Back" puts the focus on the answer the member chose. The dialog stays
  // open, so its own first focus does not run again.
  useEffect(() => {
    if (step !== "ask" || !refocus) return;
    document.querySelector<HTMLElement>(`[data-answer="${refocus}"]`)?.focus();
  }, [step, refocus]);

  // Step 2 opens with the focus on its first tick. The answer that had the
  // focus is gone, and the header's Close must not take it.
  useEffect(() => {
    if (step !== "apps") return;
    document.querySelector<HTMLElement>('[data-testid="layout-apps"] input')?.focus();
  }, [step]);

  const close = () => {
    setAsking(false);
    setReopened(false);
    setHandled(true);
    setStep("ask");
    if (newOrg) router.replace(pathname);
  };
  const next = () => (newOrg ? setStep("invite") : close());
  const again = reopened && !asking;

  /**
   * ⚠️ Every way out closes FIRST, and the write follows (round 2). A write
   * that fails must never hold the member in the question: the dialog is
   * already gone, the choice stays for this page (`keep`), and one toast
   * says what happened. The server still holds "never asked", so a later
   * visit asks again. That is the honest answer to a write that did not land.
   */
  const save = (value: Parameters<typeof saveShellPrefs>[0], askedAgain: boolean) => {
    void saveShellPrefs(value, { keep: true }).catch(() => {
      toast.show({
        key: "shell-layout-save",
        variant: "error",
        title: askedAgain
          ? "Couldn't save your layout. It shows until you reload."
          : "Couldn't save that. We'll ask again next time.",
      });
    });
  };

  // An answer goes to step 2, and saves nothing yet. Asked again with the
  // same answer, the ticks are the member's current stars. Otherwise they
  // are the answer's defaults.
  const answer = (preset: PresetId) => {
    const same = again && shell.stored?.answered === "answered" && shell.stored.preset === preset;
    const current = starredHrefs(shell.layout, sections);
    const start = same
      ? starrableHeld(sections)
          .map((p) => p.href)
          .filter((h) => current === null || current.has(h))
      : answerStars(presetById(preset)!, sections);
    setChosen(preset);
    setDefaults(start);
    setTicks(new Set(start));
    setStep("apps");
  };
  // ONE write: the answer, its preset, the stars and what the member saw.
  const finish = (shown: Iterable<string>) => {
    if (!chosen) return;
    const askedAgain = again;
    const stars = starsToSave(shown, sections, shell.layout);
    next();
    save({ ...EMPTY_SHELL, preset: chosen, answered: "answered", ...stars }, askedAgain);
  };
  const done = () => finish(ticks);
  // On a first visit the X and Escape on step 2 keep the answer, with the
  // ticks it started with. Asked again, they change nothing.
  const leaveApps = () => (again ? close() : finish(defaults));
  // Back puts the focus on the answer the member chose.
  const back = () => {
    setStep("ask");
    if (chosen) setRefocus(chosen);
  };
  const tick = (href: string) =>
    setTicks((prev) => {
      const out = new Set(prev);
      if (out.has(href)) out.delete(href);
      else out.add(href);
      return out;
    });

  // "Skip for now" is a choice, stored, so the member is not asked again.
  // It stores no stars, so every app shows. A star the member set in All
  // apps before they answered keeps its place.
  const skip = () => {
    next();
    save({ ...EMPTY_SHELL, ...shell.stored, answered: "skipped" }, false);
  };
  // Asked again from the menu, the way out keeps the layout as it is. On a
  // first visit, Escape, the header's X and "Skip for now" are all a skip.
  const dismiss = () => (again ? close() : skip());

  const current = reopened ? shell.layout.preset.id : null;
  // ⚠️ Focus an answer, never the header's Close button: Enter there would
  // skip the question (the trap `AppLauncher` records). Asked again, the
  // member's current preset; on a first visit, the first answer.
  const focusPreset = refocus ?? current ?? ANSWERS[0].preset;
  // `Button` takes no ref, so the dialog finds the answer by its mark. A
  // miss falls back to the default (`null`).
  const focusAnswer = () =>
    document.querySelector<HTMLElement>(`[data-answer="${focusPreset}"]`) ?? null;

  if (questionOpen) {
    const apps = step === "apps";
    const intro = "Tell us how you work, and we'll set up your sidebar.";
    return (
      <Modal
        open
        onClose={apps ? leaveApps : dismiss}
        title={apps ? APPS_STEP : QUESTION}
        description={
          apps
            ? "A ticked app shows in your sidebar, under its own category."
            : newOrg
              ? `Your organization is ready, and you are its owner. ${intro}`
              : `${intro} It changes the layout only, never what you can open.`
        }
        icon="Sparkles"
        size="md"
        initialFocus={focusAnswer}
      >
        {apps ? (
          <div className="flex flex-col gap-4 p-4" data-testid="layout-apps">
            <div className="flex max-h-[50vh] flex-col gap-4 overflow-y-auto">
              {appChoices(sections).map((g) => (
                <fieldset key={g.id} data-apps-group={g.id}>
                  <legend className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {g.label}
                  </legend>
                  <ul className="grid grid-cols-1 gap-0.5 sm:grid-cols-2">
                    {g.items.map((p) => (
                      <li key={p.href}>
                        <label className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-secondary">
                          <Checkbox checked={ticks.has(p.href)} onChange={() => tick(p.href)} />
                          <Icon name={p.icon} size={15} className="shrink-0 text-muted-foreground" />
                          <span className="text-sm text-foreground">{p.label}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </fieldset>
              ))}
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground" data-testid="layout-apps-later">
              You can change this any time. Star apps in{" "}
              <strong className="font-medium text-foreground">All apps</strong> (use{" "}
              <strong className="font-medium text-foreground">+ Add apps</strong> at the bottom of your sidebar), or
              use <strong className="font-medium text-foreground">Change my layout</strong> in your account menu.
            </p>
            <div className="flex justify-between gap-2">
              <Button variant="ghost" onClick={back}>
                Back
              </Button>
              <Button onClick={done}>Done</Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3 p-4" data-testid="layout-question">
            <ul className="flex flex-col gap-2">
              {ANSWERS.map((a) => {
                const names = presetExtras(presetById(a.preset)!, sections).map((p) => p.label);
                return (
                  <li key={a.preset}>
                    <Button
                      variant="secondary"
                      size="none"
                      layout="flex items-center"
                      selected={current === a.preset}
                      onClick={() => answer(a.preset)}
                      className="w-full gap-3 px-3 py-2.5 text-left"
                      data-answer={a.preset}
                    >
                      <Icon name={a.icon} size={16} className="shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-foreground">{a.label}</span>
                        <span className="block text-xs text-muted-foreground">
                          {names.length > 0
                            ? `Your Personal Center, plus ${listWords(names)}`
                            : "Your Personal Center apps"}
                        </span>
                      </span>
                    </Button>
                  </li>
                );
              })}
            </ul>
            <div className="flex justify-end">
              <Button variant="ghost" onClick={dismiss}>
                {again ? "Keep my layout" : "Skip for now"}
              </Button>
            </div>
          </div>
        )}
      </Modal>
    );
  }

  return (
    <Modal
      open={inviteOpen}
      onClose={close}
      title="Your organization is ready"
      description="You're signed in as its owner — everything you see here is yours to set up."
      icon="Sparkles"
      size="sm"
    >
      {/* The Modal primitive renders children bare — every consumer pads its
          own body (owner report 2026-08-24: this one didn't, and the text sat
          flush against the dialog edges). */}
      <InviteBody onDone={close} />
    </Modal>
  );
}

export default function WelcomeDialog() {
  return (
    <Suspense fallback={null}>
      <WelcomeDialogInner />
    </Suspense>
  );
}
