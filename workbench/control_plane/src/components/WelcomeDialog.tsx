"use client";

/**
 * WelcomeDialog — the first-run moment, and the first sign-in question.
 *
 * Two jobs, in ONE dialog, so a member never sees two in a row:
 *
 * 1. **The question** (NS-7, `navigation_shell.md` §8.4). "What will you do
 *    most here?", with six answers and "Skip for now". The answer picks a
 *    preset (`presets.ts`), which arranges the sidebar's "My apps", My Day's
 *    cards and the command bar's jobs. It changes the layout and nothing else.
 *    Every member is asked on their first visit, invited members too: the
 *    trigger is a layout the server holds as "never asked" (`answered` null).
 *    "Skip for now" is stored as a choice, so nobody is asked twice. "Change
 *    my layout" in the account menu asks again (`CHANGE_LAYOUT`).
 * 2. **The founder's welcome** (CP-2c, owner directive 2026-08-24). Armed by
 *    `?welcome=new-org`, which `SignUpForm` appends to its post-create
 *    redirect. It says "your organization is ready" and "here is where you
 *    add your team". With the question on, it is the step after the answer.
 *
 * ⚠️ A read that fails asks nothing. "Never asked" is a fact only the server
 * knows, and asking on a fault would ask a member who already answered.
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
import Modal from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { visibleSections } from "@/lib/nav";
import { ANSWERS, EMPTY_SHELL, QUESTION, pinnedPanes, presetById, type PresetId } from "@/lib/shell/presets";
import { shellNavOn } from "@/lib/shell/shellNav";
import { CHANGE_LAYOUT, askedInThisPage, markAsked, saveShellPrefs, useShellPrefs } from "@/lib/shell/shellPrefs";

export const WELCOME_PARAM = "welcome";
export const WELCOME_NEW_ORG = "new-org";

/** "Projects, Approvals and Chat". */
function listWords(words: readonly string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
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
  const [step, setStep] = useState<"ask" | "invite">("ask");
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

  const questionOpen = navOn && (asking || reopened) && step === "ask";
  // The founder's welcome: after the question, or alone when there is none.
  // It waits for the layout read, or it would show first and then give way
  // to the question it should follow.
  const inviteOpen =
    newOrg && !questionOpen && !(navOn && shell.loading) && (step === "invite" || !(asking || reopened));

  const close = () => {
    setAsking(false);
    setReopened(false);
    setHandled(true);
    setStep("ask");
    if (newOrg) router.replace(pathname);
  };
  const next = () => (newOrg ? setStep("invite") : close());

  /**
   * ⚠️ Every way out closes FIRST, and the write follows (round 2). A write
   * that fails must never hold the member in the question: the dialog is
   * already gone, the choice stays for this page (`keep`), and one toast
   * says what happened. The server still holds "never asked", so a later
   * visit asks again. That is the honest answer to a write that did not land.
   */
  const save = (value: Parameters<typeof saveShellPrefs>[0], again: boolean) => {
    void saveShellPrefs(value, { keep: true }).catch(() => {
      toast.show({
        key: "shell-layout-save",
        variant: "error",
        title: again
          ? "Couldn't save your layout. It shows until you reload."
          : "Couldn't save that. We'll ask again next time.",
      });
    });
  };
  const answer = (preset: PresetId) => {
    const again = reopened && !asking;
    next();
    save({ ...EMPTY_SHELL, preset, answered: "answered" }, again);
  };
  // "Skip for now" is a choice, stored, so the member is not asked again. A
  // pin the member set before keeps its place.
  const skip = () => {
    next();
    save({ ...EMPTY_SHELL, ...shell.stored, answered: "skipped" }, false);
  };
  // Asked again from the menu, the way out keeps the layout as it is. On a
  // first visit, Escape, the header's X and "Skip for now" are all a skip.
  const dismiss = () => (reopened && !asking ? close() : skip());

  const current = reopened ? shell.layout.preset.id : null;
  // ⚠️ Focus an answer, never the header's Close button: Enter there would
  // skip the question (the trap `AppLauncher` records). Asked again, the
  // member's current preset; on a first visit, the first answer.
  const focusPreset = current ?? ANSWERS[0].preset;
  // `Button` takes no ref, so the dialog finds the answer by its mark. A
  // miss falls back to the default (`null`).
  const focusAnswer = () =>
    document.querySelector<HTMLElement>(`[data-answer="${focusPreset}"]`) ?? null;

  if (questionOpen) {
    return (
      <Modal
        open
        onClose={dismiss}
        title={QUESTION}
        description={
          newOrg
            ? "Your organization is ready, and you are its owner. Pick the closest answer, and Metorite arranges your sidebar and My Day for it."
            : "Pick the closest answer, and Metorite arranges your sidebar and My Day for it. It changes the layout only. You can change it later from your account menu."
        }
        icon="Sparkles"
        size="md"
        initialFocus={focusAnswer}
      >
        <div className="flex flex-col gap-3 p-4" data-testid="layout-question">
          <ul className="flex flex-col gap-2">
            {ANSWERS.map((a) => {
              const preset = presetById(a.preset)!;
              const names = pinnedPanes(preset.pins, sections).map((p) => p.label);
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
                        {names.length > 0 ? `Pins ${listWords(names)}` : "Arranges My Day for this work"}
                      </span>
                    </span>
                  </Button>
                </li>
              );
            })}
          </ul>
          <div className="flex justify-end">
            <Button variant="ghost" onClick={dismiss}>
              {reopened && !asking ? "Keep my layout" : "Skip for now"}
            </Button>
          </div>
        </div>
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
