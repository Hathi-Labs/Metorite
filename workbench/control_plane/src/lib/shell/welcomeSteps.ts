/**
 * The rules of the first sign-in dialog that need no React
 * (`components/WelcomeDialog.tsx`, `navigation_shell.md` §8.4). Pure, so
 * each rule is an assertion. Fence: `welcomeSteps.test.ts`.
 */

/**
 * Whether the question dialog shows.
 *
 * ⚠️ Never while access resolves. Step 2 lists the apps the member holds,
 * and before access resolves that list is empty, so "Done" would save
 * `pins: []` and hide every app.
 */
export function layoutQuestionOpen(a: {
  navOn: boolean;
  asking: boolean;
  reopened: boolean;
  step: "ask" | "apps" | "invite";
  accessLoading: boolean;
}): boolean {
  return a.navOn && !a.accessLoading && (a.asking || a.reopened) && (a.step === "ask" || a.step === "apps");
}

