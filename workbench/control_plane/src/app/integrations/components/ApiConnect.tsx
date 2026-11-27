"use client";

/**
 * API connections in the console (API-5, `api_connections.md` §2.9 and §2.11).
 *
 * Four parts, one file, because they are one flow:
 *
 * - `ConnectApiModal` is "Connect an API". The API keys view opens it in
 *   place of "Add API" when the gateway serves Zoho Books to this admin. It
 *   lists the T1 APIs of the connector list first. Connect asks the BFF for
 *   the Zoho consent URL, and the browser goes there. Under the list, the old
 *   custom path stays.
 * - `ApiConnectReturn` lives in the Connections view, where Zoho sends the
 *   browser back. `?api_pick=` opens the pick sheet. `?api_connected=` reads
 *   the gateway status, and shows the result card only when the status says
 *   connected. `?api_connect_error=` shows its one sentence. Then the address
 *   is cleared.
 * - `ConnectResultCardView` is the result card of IN-9. It is never a modal.
 * - `UsedBySection` is the "used by" list of IN-10 in the connection sheet.
 *
 * `lib/apiConnect.ts` holds every decision and every word. This file only
 * draws them. Fences: `lib/apiConnect.test.ts` and `ApiConnect.test.tsx`.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { useViewMode } from "@/components/ViewModeProvider";
import type { Connector } from "@/lib/connectors";

import * as api from "../lib/api";
import {
  API_CONNECT_LABEL,
  BOOKS_PROVIDER,
  CUSTOM_API_LINE,
  apiReturn,
  chatLaterLine,
  apiRowState,
  pickErrorText,
  pickIsOver,
  resultCardView,
  returnDecision,
  t1Apis,
  withoutApiReturn,
  type ApiRowState,
  type BooksChoice,
  type BooksStatus,
  type ResultCard,
  type UsedBy,
} from "../lib/apiConnect";
import { ConnectionState } from "./ConnectionList";

// ── The row of one T1 API ─────────────────────────────────────────────────

export function ApiConnectorRow({
  connector,
  state,
  busy,
  disabled,
  error,
  onConnect,
}: {
  connector: Connector;
  state: ApiRowState;
  busy?: boolean;
  disabled?: boolean;
  error?: string | null;
  onConnect: () => void;
}) {
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3" data-api-connector={connector.id}>
      <div className="flex items-start gap-3">
        <Icon name="Plug" className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-foreground">{connector.label}</span>
            {state === "not_connected" ? null : (
              <ConnectionState status={state === "connected" ? "connected" : "needs_reconnect"} />
            )}
          </div>
          <p className="break-words text-xs text-muted-foreground">{connector.unlocks}</p>
          {chatLaterLine(connector) ? (
            <p className="break-words text-xs text-muted-foreground" data-chat-later="">
              {chatLaterLine(connector)}
            </p>
          ) : null}
        </div>
        <Button
          variant={state === "connected" ? "secondary" : "primary"}
          size="sm"
          icon={state === "connected" ? "RefreshCw" : "LogIn"}
          loading={busy}
          disabled={disabled}
          onClick={onConnect}
          data-api-connect={state}
        >
          {API_CONNECT_LABEL[state]}
        </Button>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </li>
  );
}

/** The words of the modal: whose Zoho rights the connection carries (D-API-3). */
export const CONNECT_API_NOTE =
  "Metorite signs in to Zoho as you. The connection reads what your Zoho account can read. " +
  "When the chat can read it, only admins can use it there.";

/** The body of "Connect an API": the T1 list, then the old custom path. */
export function ConnectApiBody({
  apis,
  status,
  busyId,
  error,
  onConnect,
  onCustom,
}: {
  apis: readonly Connector[];
  status: BooksStatus | null;
  busyId: string | null;
  error: string | null;
  onConnect: (id: string) => void;
  onCustom: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 p-4">
      <p className="text-xs text-muted-foreground">{CONNECT_API_NOTE}</p>
      <ul className="flex flex-col gap-2" aria-label="APIs that Metorite connects">
        {apis.map((c) => (
          <ApiConnectorRow
            key={c.id}
            connector={c}
            state={c.id === BOOKS_PROVIDER ? apiRowState(status) : "not_connected"}
            busy={busyId === c.id}
            disabled={busyId !== null && busyId !== c.id}
            error={busyId === null ? error : null}
            onConnect={() => onConnect(c.id)}
          />
        ))}
      </ul>
      <div className="flex flex-col gap-2 border-t border-border pt-3">
        <p className="text-xs text-muted-foreground">{CUSTOM_API_LINE}</p>
        <div>
          <Button variant="secondary" size="sm" icon="Plus" onClick={onCustom}>
            Add another API
          </Button>
        </div>
      </div>
    </div>
  );
}

/** "Connect an API", in the one dialog primitive. */
export function ConnectApiModal({
  status,
  onClose,
  onCustom,
}: {
  status: BooksStatus | null;
  onClose: () => void;
  onCustom: () => void;
}) {
  const { isMobile } = useViewMode();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function connect(id: string) {
    // Zoho Books is the one T1 API with a connect flow today (API-2).
    if (id !== BOOKS_PROVIDER) return;
    setBusyId(id);
    setError(null);
    try {
      const url = await api.startBooks();
      // The URL is the gateway's, checked by the BFF: https, no user info.
      window.location.assign(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Metorite cannot start the Zoho sign-in. Try again.");
      setBusyId(null);
    }
  }

  return (
    <Modal
      open
      onClose={() => {
        if (busyId === null) onClose();
      }}
      title="Connect an API"
      description="Connect a system that Metorite knows."
      size="md"
      placement={isMobile ? "sheet" : "center"}
      closeLabel="Close Connect an API"
    >
      <ConnectApiBody
        apis={t1Apis()}
        status={status}
        busyId={busyId}
        error={error}
        onConnect={(id) => void connect(id)}
        onCustom={onCustom}
      />
    </Modal>
  );
}

// ── The pick of a Zoho Books organization ─────────────────────────────────

export function BooksPickBody({
  choices,
  pickingId,
  error,
  over,
  restarting,
  onPick,
  onStartAgain,
}: {
  choices: readonly BooksChoice[];
  pickingId: string | null;
  error: string | null;
  /** True when the pick cannot go on, so the sheet offers a new connect. */
  over: boolean;
  /** True while the new connect starts: the button waits, and a second click does nothing. */
  restarting?: boolean;
  onPick: (id: string) => void;
  onStartAgain: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 p-4">
      <p className="text-xs text-muted-foreground">
        Your Zoho account has {choices.length} Zoho Books organizations. Pick the one that the chat reads.
      </p>
      <ul className="flex flex-col gap-2" aria-label="Zoho Books organizations">
        {choices.map((c) => (
          <li
            key={c.id}
            className="flex items-center gap-3 rounded-lg border border-border bg-card p-3"
            data-books-choice={c.id}
          >
            <Icon name="Building2" className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <div className="break-words text-sm text-foreground">{c.name}</div>
              <div className="text-[11px] text-muted-foreground">Organization ID {c.id}</div>
            </div>
            <Button
              variant="secondary"
              size="sm"
              loading={pickingId === c.id}
              disabled={over || (pickingId !== null && pickingId !== c.id)}
              onClick={() => onPick(c.id)}
            >
              Use this one
            </Button>
          </li>
        ))}
      </ul>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {over ? (
        <div>
          <Button
            variant="primary"
            size="sm"
            icon="LogIn"
            loading={restarting}
            disabled={restarting}
            onClick={onStartAgain}
            data-books-start-again=""
          >
            Connect Zoho Books again
          </Button>
        </div>
      ) : null}
    </div>
  );
}

// ── The result card (IN-9) ────────────────────────────────────────────────

export function ConnectResultCardView({
  card,
  onNext,
  onDismiss,
}: {
  card: ResultCard;
  onNext: (href: string) => void;
  onDismiss: () => void;
}) {
  const view = resultCardView(card);
  return (
    <section
      role="status"
      aria-label={view.title}
      className="mx-4 mt-3 flex items-start gap-3 rounded-lg border border-border bg-card p-3"
      data-result-card={card.provider}
    >
      <Icon name="CircleCheck" className="mt-0.5 h-4 w-4 shrink-0 text-success" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-sm font-medium text-foreground">{view.title}</p>
        {view.lines.map((line) => (
          <p key={line} className="break-words text-xs text-muted-foreground">
            {line}
          </p>
        ))}
        {view.next ? (
          <div className="pt-1">
            <Button variant="secondary" size="sm" icon="ArrowRight" onClick={() => onNext(view.next!.href)}>
              {view.next.label}
            </Button>
          </div>
        ) : null}
      </div>
      <Button variant="ghost" size="icon-sm" icon="X" aria-label="Close the result" onClick={onDismiss} />
    </section>
  );
}

// ── Used by (IN-10) ───────────────────────────────────────────────────────

export function UsedBySection({ usedBy }: { usedBy: UsedBy }) {
  return (
    <div className="flex flex-col gap-1" data-used-by="">
      <span className="text-xs text-muted-foreground">Used by</span>
      {usedBy.names.length > 0 ? (
        <ul className="flex flex-col gap-0.5 text-sm text-foreground">
          {usedBy.names.map((n) => (
            <li key={n} className="break-words">
              {n}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-foreground">{usedBy.line}</p>
      )}
    </div>
  );
}

// ── The return from Zoho ──────────────────────────────────────────────────

/**
 * Reads the return of the Zoho Books consent in the Connections view. Only
 * the gateway status decides: a link that names a return does nothing for an
 * org that the flag does not hold, because the status route is then 404.
 */
export function ApiConnectReturn({ onConnected }: { onConnected: () => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const toast = useToast();
  const { isMobile } = useViewMode();
  const ret = apiReturn(searchParams);
  const handled = useRef<string | null>(null);
  const [pick, setPick] = useState<{ handle: string; choices: BooksChoice[] } | null>(null);
  const [pickingId, setPickingId] = useState<string | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  const [pickOver, setPickOver] = useState(false);
  const [card, setCard] = useState<ResultCard | null>(null);
  const [restarting, setRestarting] = useState(false);

  const clear = () =>
    router.replace(withoutApiReturn(pathname || "/integrations", new URLSearchParams(searchParams)), {
      scroll: false,
    });

  const key = ret ? searchParams.toString() : null;
  useEffect(() => {
    if (!ret || key === null || handled.current === key) return;
    handled.current = key;
    let live = true;
    void (async () => {
      let status: BooksStatus | null = null;
      try {
        status = ret.kind === "pick" ? await api.getBooksPick(ret.pick) : await api.getBooksStatus();
      } catch {
        status = null;
      }
      if (!live) return;
      // `returnDecision` holds every rule. A null status (the route is dark,
      // or this member may not manage it) does nothing at all.
      const next = returnDecision(ret, status);
      if (next.kind === "pick" && ret.kind === "pick") {
        setPick({ handle: ret.pick, choices: next.choices });
        return;
      }
      if (next.kind === "card") {
        setCard(next.card);
        onConnected();
      } else if (next.kind === "toast") {
        toast.show({ key: "integrations-api-connect", variant: "error", title: next.text });
      }
      clear();
    })();
    return () => {
      live = false;
    };
    // `clear` and `toast` change on each render. The key holds the one return.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // P2 of the review: a tab that closes with the sheet open asks the gateway
  // to revoke the pending token. Best effort: a beacon, never a wait.
  const openHandle = pick?.handle ?? null;
  useEffect(() => {
    if (openHandle === null) return;
    const onHide = () => api.cancelBooksBeacon(openHandle);
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [openHandle]);

  async function choose(id: string) {
    if (!pick) return;
    setPickingId(id);
    setPickError(null);
    try {
      const status = await api.pickBooks(pick.handle, id);
      setPick(null);
      clear();
      if (status.connected && status.card) {
        setCard(status.card);
        onConnected();
      }
    } catch (err) {
      const code = (err as api.IntegrationsError | null)?.code;
      setPickError(err instanceof Error ? err.message : pickErrorText(code));
      setPickOver(pickIsOver(code));
    } finally {
      setPickingId(null);
    }
  }

  async function startAgain() {
    setRestarting(true);
    try {
      window.location.assign(await api.startBooks());
    } catch (err) {
      setPickError(err instanceof Error ? err.message : pickErrorText(null));
      setRestarting(false);
    }
  }

  return (
    <>
      {card ? (
        <ConnectResultCardView card={card} onNext={(href) => router.push(href)} onDismiss={() => setCard(null)} />
      ) : null}
      {pick ? (
        <Modal
          open
          onClose={() => {
            if (pickingId !== null || restarting) return;
            // P2: the admin leaves the pick, so the gateway revokes its token.
            // Best effort: closing never waits for it.
            if (pick && !pickOver) void api.cancelBooks(pick.handle);
            setPick(null);
            setPickError(null);
            setPickOver(false);
            clear();
          }}
          title="Pick a Zoho Books organization"
          description="Metorite saves nothing until you pick one."
          size="md"
          placement={isMobile ? "sheet" : "center"}
          closeLabel="Close the pick"
        >
          <BooksPickBody
            choices={pick.choices}
            pickingId={pickingId}
            error={pickError}
            over={pickOver}
            restarting={restarting}
            onPick={(id) => void choose(id)}
            onStartAgain={() => void startAgain()}
          />
        </Modal>
      ) : null}
    </>
  );
}
