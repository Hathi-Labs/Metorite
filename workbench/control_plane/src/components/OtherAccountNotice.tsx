"use client";

import { SwitchingCover, useAccounts, useSwitchAccount } from "@/components/AccountSwitcher";
import { ErrorCardView } from "@/components/ChatErrorCard";
import { isActiveAccount, pressSwitch, slotFor, type Accounts } from "@/lib/accountSwitch";
import { OTHER_ACCOUNT_ACTIVE_WORDS, otherAccountSwitchWords, type RunErrorView } from "@/lib/runErrors";

/**
 * The other-account notice (WS-51 S2) as the thread mounts it. Owner call,
 * 2026-10-18: dismissing it left the member stuck, because only the other
 * account can answer the question.
 *
 * - This browser holds a slot for that account: a card with a "Switch to
 *   {account}" button. The button is the account menu's own switch
 *   (`useSwitchAccount`), with the same cover and the same full reload, and
 *   the card stays until the member acts.
 * - No slot, or the switcher flag is off: the info-only line, which a member
 *   may dismiss, and which says to sign in from the account menu.
 * - That account is the active one now: the info-only line that says to
 *   answer the question again.
 *
 * ⚠️ A press reads the accounts AGAIN before it switches (`pressSwitch`,
 * review of #1131). A switch in another tab can move an account to another
 * slot, so the slot read at mount can name the wrong account.
 *
 * It draws nothing until the first read of the accounts answers, so the
 * line never jumps into a card. Its only hooks are the switcher's two, so a
 * test can call it with those two mocked.
 *
 * ⚠️ It lives in its own file, and `MessageBubble` mounts it, so the error
 * card does not import the account switcher. That import made each fresh
 * load of `ChatErrorCard` about half again as slow, and the suites that load
 * it once per test ran past their timeout under the full run.
 *
 * Fence: `otherAccountNotice.test.ts`.
 */
export function OtherAccountNotice({ error, id, dismissKey }: { error: RunErrorView; id?: string; dismissKey?: string }) {
  const { accounts, reload, loaded } = useAccounts();
  const { switching, error: switchError, pick } = useSwitchAccount(() => void reload(false));
  if (!loaded) return null;
  return (
    <>
      <OtherAccountNoticeView
        error={error}
        accounts={accounts}
        switchError={switchError}
        onSwitch={() => void pressSwitch(error.raw, () => reload(false), pick)}
        id={id}
        dismissKey={dismissKey}
      />
      {switching && <SwitchingCover email={switching} />}
    </>
  );
}

/**
 * The other-account notice, given the accounts this browser holds. Pure,
 * with no hook, so a test can read its tree and press its button. The
 * button names the address as the body does, from the notice's `raw`.
 */
export function OtherAccountNoticeView({
  error,
  accounts,
  onSwitch,
  switchError,
  id,
  dismissKey,
}: {
  error: RunErrorView;
  accounts: Accounts;
  onSwitch: () => void;
  /** Why the last switch failed, from `useSwitchAccount`. */
  switchError?: string | null;
  id?: string;
  dismissKey?: string;
}) {
  if (isActiveAccount(accounts, error.raw)) {
    return (
      <ErrorCardView
        error={error}
        words={OTHER_ACCOUNT_ACTIVE_WORDS}
        id={id}
        dismissKey={dismissKey ? `${dismissKey}:active` : undefined}
      />
    );
  }
  const target = slotFor(accounts, error.raw);
  if (!target) return <ErrorCardView error={error} id={id} dismissKey={dismissKey} />;
  return (
    <>
      <ErrorCardView
        error={error}
        words={otherAccountSwitchWords(error.raw.trim() || target.email)}
        onRetry={onSwitch}
        id={id}
      />
      {switchError && (
        <p role="alert" data-switch-error className="mt-1 min-w-0 text-[11px] text-destructive wrap-anywhere">
          {switchError}
        </p>
      )}
    </>
  );
}
