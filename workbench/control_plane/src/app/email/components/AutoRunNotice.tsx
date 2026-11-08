"use client";

/**
 * The Auto-run notice of one Gmail mailbox (WS-17 EM-G11c2, task 2267,
 * `email_app_master_plan.md` §12.3.13.7).
 *
 * It draws in the mailbox notice stack of the mail pane, beside the sync
 * banner and `StorageNotice`, with the look of `StorageNotice`: the neutral
 * card surface, the `Info` icon and the same row. It is not a modal.
 *
 * Two parts, because vitest here runs in the node environment:
 * - `AutoRunNoticeView` is pure. A test draws it with `createElement`.
 * - `AutoRunNotice` reads through `lib/autoRunNotice.ts`, which decides who
 *   sees it. The read makes no write. Only "Turn on" saves.
 */

import { useEffect, useState } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { getAssistantSettings, listRules, saveAssistantSettings } from "../lib/api";
import {
  AUTO_RUN_NOTICE_COPY as COPY,
  autoRunNoticeText,
  loadAutoRunNotice,
  rememberAutoRunNotNow,
  turnOnAutoRun,
  type AutoRunNoticeApi,
} from "../lib/autoRunNotice";
import type { EmailAccount } from "../lib/types";

const NOTICE_API: AutoRunNoticeApi = { listRules, getAssistantSettings, saveAssistantSettings };

export function AutoRunNoticeView({
  address,
  count,
  busy,
  error,
  onTurnOn,
  onNotNow,
}: {
  address: string;
  count: number;
  busy: boolean;
  error: string | null;
  onTurnOn: () => void;
  onNotNow: () => void;
}) {
  return (
    <section
      aria-label={`Rules for new mail, ${address}`}
      // `flex-wrap` with a basis on the text: on a phone the buttons drop below
      // the words instead of squeezing them (the StorageNotice rule).
      className="flex flex-wrap items-start gap-x-2 gap-y-1.5 px-3 py-2 border-b border-border bg-card flex-shrink-0"
    >
      <Icon name="Info" size={14} className="mt-0.5 flex-shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1 basis-48">
        <p className="text-xs text-foreground">{autoRunNoticeText(count, address)}</p>
        {error && (
          <p role="alert" className="mt-1 text-[11px] text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="ml-auto flex flex-shrink-0 items-center gap-2">
        <Button variant="secondary" size="sm" loading={busy} disabled={busy} onClick={onTurnOn}>
          {COPY.turnOn}
        </Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={onNotNow}>
          {COPY.notNow}
        </Button>
      </div>
    </section>
  );
}

export function AutoRunNotice({ account }: { account: Pick<EmailAccount, "id" | "provider" | "emailAddress"> }) {
  // null: nothing to show (yet). The page keys this on the account id.
  const [count, setCount] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accountId = account.id;
  const provider = account.provider;

  useEffect(() => {
    let live = true;
    void loadAutoRunNotice(NOTICE_API, { id: accountId, provider }).then((n) => live && setCount(n));
    return () => {
      live = false;
    };
  }, [accountId, provider]);

  if (count === null) return null;

  const turnOn = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await turnOnAutoRun(NOTICE_API, account.id);
      setCount(null);
    } catch {
      setError(COPY.failed);
      setBusy(false);
    }
  };

  return (
    <AutoRunNoticeView
      address={account.emailAddress}
      count={count}
      busy={busy}
      error={error}
      onTurnOn={() => void turnOn()}
      onNotNow={() => {
        rememberAutoRunNotNow(account.id);
        setCount(null);
      }}
    />
  );
}
