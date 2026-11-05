"use client";

/**
 * The consent screen an outside agent opens (WS-55 EA-4a, spec §10.5).
 *
 * The flow, each hop a rule of the spec:
 *
 * 1. The page posts the agent's request to `/api/agent-access/authorize/check`.
 *    The answer names the client, its verified origin, `redirect_display`,
 *    the requested scopes and a one-time consent ticket.
 * 2. The member may clear a scope, pin projects, and opt in to personal
 *    projects. `offline_access` has no box.
 * 3. Approve posts the SAME request, the ticket and the choices to
 *    `/api/agent-access/authorize/approve`. The answer is JSON `redirect_to`,
 *    and the page goes there with `window.location.assign`. Deny goes to
 *    `deny_redirect_to` from step 1.
 *
 * D-EA-11: when step 1 answers `first_party: true` with `redirect_to`, the
 * gateway already minted a code for a first-party client that the member
 * approved before. The page goes there at once and draws no form. The
 * gateway alone decides that (`isContinueView` in `consent.ts`).
 *
 * The organization is the member's current session's (EA3-25), read through
 * `useAccess()`. Approve binds that organization, so Approve waits until
 * the page can name it.
 *
 * Design: rules 1 to 3 and rule 8 of `workbench/control_plane/AGENTS.md`.
 * Tokens only, `<Icon>`, and the primitives of `src/components/ui/`. The
 * decisions live in `consent.ts`, and this file only draws them.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import AccessTree from "@/components/AccessTree";
import Tabs from "@/components/Tabs";
import Badge from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import {
  NO_SELECTION,
  buildProjectTree,
  cleanSelection,
  grantSummary,
  projectLists,
  projectsProblem,
  selectedCount,
  type ProjectMode,
  type ProjectTree,
  type Selection,
} from "@/lib/accessTree";

import {
  type AuthorizeRequest,
  type CheckView,
  type ParsedRequest,
  type Refusal,
  approveBody,
  canApprove,
  gateOpen,
  gateStart,
  gateWaitMs,
  initialChoice,
  isCheckView,
  isContinueView,
  OFFLINE_ACCESS,
  grantedScopes,
  organizationName,
  refusalFor,
  sameScreen,
  scopeRows,
  toggleScope,
} from "./consent";

type Phase =
  | { kind: "checking" }
  | { kind: "refused"; refusal: Refusal }
  | { kind: "ready"; view: CheckView }
  | { kind: "leaving"; message: string };

const CHECK_URL = "/api/agent-access/authorize/check";

/**
 * The end of the flow when approve's answer is lost (fix round 2). The
 * gateway may have made the connection, so the page must not try again.
 */
const UNCONFIRMED: Refusal = {
  message:
    "We could not confirm the answer. Start again from the agent, and check your connections if it asks twice.",
  redirectTo: null,
};
const APPROVE_URL = "/api/agent-access/authorize/approve";

async function postJson(url: string, body: unknown, signal?: AbortSignal) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  let json: unknown = null;
  let parsed = false;
  try {
    json = await res.json();
    parsed = true;
  } catch {
    json = null;
  }
  return { status: res.status, ok: res.ok, json, parsed };
}

/**
 * The approve gate (fix round 1, P1). Approve stays off until the page is
 * visible and focused, and then 500 ms more. A blur or a hide starts again.
 * The decision is `gateStart` and `gateOpen` in `consent.ts`. This hook only
 * feeds them the browser's events. `openNow` reads the clock at the click,
 * so a stale render cannot approve.
 */
function useApproveGate(): { open: boolean; openNow: () => boolean } {
  const [start, setStart] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    const visible = () => document.visibilityState === "visible";
    const apply = (focused: boolean) =>
      setStart((prev) => {
        const next = gateStart(prev, { visible: visible(), focused }, Date.now());
        startRef.current = next;
        return next;
      });
    apply(document.hasFocus());
    const onFocus = () => apply(true);
    const onBlur = () => apply(false);
    const onVisibility = () => apply(visible() && document.hasFocus());
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // Draw again when the gate opens.
  useEffect(() => {
    const wait = gateWaitMs(start, Date.now());
    if (wait === null) return;
    const timer = setTimeout(() => setNow(Date.now()), wait);
    return () => clearTimeout(timer);
  }, [start]);

  const openNow = useCallback(() => gateOpen(startRef.current, Date.now()), []);
  // `now` moves only by the timer, so a new spell (a later `start`) reads closed.
  return { open: gateOpen(start, now), openNow };
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full items-start justify-center p-4 sm:items-center sm:p-8">
      <div className="w-full max-w-lg rounded-lg border border-border bg-card shadow-lg">{children}</div>
    </div>
  );
}

function Message({ icon, tone, title, body, action }: {
  icon: string;
  tone: string;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <Frame>
      <div className="flex flex-col items-center gap-3 p-6 text-center" role="status">
        <span className={`flex h-12 w-12 items-center justify-center rounded-full ${tone}`}>
          <Icon name={icon} size={24} />
        </span>
        <div>
          <h1 className="text-base font-semibold text-foreground">{title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{body}</p>
        </div>
        {action}
      </div>
    </Frame>
  );
}

export default function ConsentScreen({ parsed }: { parsed: ParsedRequest }) {
  const [phase, setPhase] = useState<Phase>(() =>
    parsed.ok
      ? { kind: "checking" }
      : { kind: "refused", refusal: { message: parsed.message, redirectTo: null } },
  );
  const request: AuthorizeRequest | null = parsed.ok ? parsed.request : null;

  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    (async () => {
      try {
        const res = await postJson(CHECK_URL, request, controller.signal);
        if (res.ok && isContinueView(res.json)) {
          // D-EA-11: a first-party client that the member already approved.
          // The gateway minted the code, so no screen shows and no click is
          // asked. Every other client gets the screen and its approve gate.
          setPhase({ kind: "leaving", message: "Signing you in." });
          window.location.assign(res.json.redirect_to);
          return;
        }
        if (res.ok && isCheckView(res.json)) {
          setPhase({ kind: "ready", view: res.json });
        } else {
          setPhase({ kind: "refused", refusal: refusalFor(res.ok ? 502 : res.status, res.json) });
        }
      } catch {
        if (!controller.signal.aborted) {
          setPhase({ kind: "refused", refusal: refusalFor(0, null) });
        }
      }
    })();
    return () => controller.abort();
    // The request comes from the URL once. A new URL is a new page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase.kind === "checking") {
    return (
      <Frame>
        <div className="space-y-3 p-6" aria-busy="true" aria-label="Checking the agent">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-24 w-full" />
        </div>
      </Frame>
    );
  }
  if (phase.kind === "refused") {
    const { refusal } = phase;
    return (
      <Message
        icon="AlertCircle"
        tone="bg-warning/10 text-warning"
        title="This agent cannot connect"
        body={refusal.message}
        action={
          refusal.redirectTo ? (
            <Button
              variant="secondary"
              icon="ArrowLeft"
              onClick={() => window.location.assign(refusal.redirectTo as string)}
            >
              Return to the agent
            </Button>
          ) : undefined
        }
      />
    );
  }
  if (phase.kind === "leaving") {
    return (
      <Message icon="ArrowUpRight" tone="bg-primary/10 text-primary" title="Returning to the agent" body={phase.message} />
    );
  }
  return (
    <ConsentForm
      request={request as AuthorizeRequest}
      view={phase.view}
      onLeave={(message, url) => {
        setPhase({ kind: "leaving", message });
        window.location.assign(url);
      }}
      onRefused={(refusal) => setPhase({ kind: "refused", refusal })}
    />
  );
}

function ConsentForm({ request, view, onLeave, onRefused }: {
  request: AuthorizeRequest;
  view: CheckView;
  onLeave: (message: string, url: string) => void;
  onRefused: (refusal: Refusal) => void;
}) {
  const { access, loading: accessLoading } = useAccess();
  const orgName = organizationName(access.organization);
  const rows = useMemo(() => scopeRows(view.scopes).filter((r) => !r.fixed), [view.scopes]);
  const [chosen, setChosen] = useState<Set<string>>(() => initialChoice(view));
  const [mode, setMode] = useState<ProjectMode>("all");
  const [selection, setSelection] = useState<Selection>(NO_SELECTION);
  // On by default (owner 2026-10-30, D-EA-4 as amended). The member can untick it.
  const [includePersonal, setIncludePersonal] = useState(true);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [tree, setTree] = useState<ProjectTree | null>(null);
  const [projectsFailed, setProjectsFailed] = useState(false);
  const gate = useApproveGate();
  // The screen the member approves. After a 503 the page asks `check` again
  // for a fresh ticket, and keeps it only for the same screen (fix round 1).
  const [shown, setShown] = useState<CheckView>(view);
  const [needsFreshTicket, setNeedsFreshTicket] = useState(false);

  // One read, once. The page lives for one approval, so a cache adds nothing.
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/projects/tree", { cache: "no-store", signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        setTree(buildProjectTree(await res.json()));
      })
      .catch(() => {
        if (!controller.signal.aborted) setProjectsFailed(true);
      });
    return () => controller.abort();
  }, []);

  const toggle = (scope: string) => setChosen((prev) => toggleScope(view, prev, scope));
  const lists = projectLists(mode, tree ? cleanSelection(tree, selection) : NO_SELECTION);
  const projectIssue = projectsProblem(mode, tree, selection);
  const summary = grantSummary({
    who: view.client.host,
    scopes: grantedScopes(view, chosen),
    mode,
    picked: tree ? selectedCount(tree, selection) : 0,
    includePersonal,
    offline: view.scopes.includes(OFFLINE_ACCESS),
  });

  const ready = canApprove(view, chosen) && !projectIssue && !!orgName && !sending && gate.open;

  /** A fresh ticket for the same screen, or null when the flow must end. */
  /** The fresh check is busy: no approve went out, so one more try is safe. */
  function checkBusy(): null {
    setProblem(refusalFor(503, null).message);
    setSending(false);
    return null;
  }

  async function freshTicket(): Promise<CheckView | null> {
    let res: Awaited<ReturnType<typeof postJson>>;
    try {
      res = await postJson(CHECK_URL, request);
    } catch {
      return checkBusy();
    }
    if (res.status === 503) return checkBusy();
    if (!res.ok || !isCheckView(res.json)) {
      onRefused(refusalFor(res.ok ? 502 : res.status, res.json));
      return null;
    }
    if (!sameScreen(shown, res.json)) {
      onRefused({ message: "The agent's request changed. Start again from the agent.", redirectTo: null });
      return null;
    }
    setShown(res.json);
    setNeedsFreshTicket(false);
    return res.json;
  }

  async function approve() {
    if (!ready || !gate.openNow()) return;
    setSending(true);
    setProblem(null);
    // Redis may have spent the old ticket before its reply timed out, so a
    // retry after a gateway 503 starts with a fresh ticket.
    const ticketView = needsFreshTicket ? await freshTicket() : shown;
    if (!ticketView) return;
    let res: Awaited<ReturnType<typeof postJson>>;
    try {
      res = await postJson(
        APPROVE_URL,
        approveBody(request, ticketView, {
          scopes: chosen,
          pins: lists.pins,
          leftOut: lists.leftOut,
          includePersonal,
        }),
      );
    } catch {
      // ⚠️ The answer is lost, and the gateway may have made the connection.
      // A retry would make a SECOND one, so the flow ends (fix round 2).
      onRefused(UNCONFIRMED);
      return;
    }
    const redirectTo = (res.json as { redirect_to?: unknown } | null)?.redirect_to;
    if (res.ok && typeof redirectTo === "string") {
      onLeave(`${view.client.host} is connected.`, redirectTo);
      return;
    }
    if (!res.parsed || res.ok) {
      // Not JSON, or a 2xx with no redirect: nobody knows what happened.
      onRefused(UNCONFIRMED);
      return;
    }
    if (res.status === 503) {
      // Every 503 of approve comes BEFORE `create_connection` (the ticket
      // spend), so a retry with a fresh ticket makes no second connection.
      setNeedsFreshTicket(true);
      setProblem(refusalFor(503, res.json).message);
      setSending(false);
      return;
    }
    onRefused(refusalFor(res.status, res.json));
  }

  return (
    <Frame>
      <div className="border-b border-border p-6">
        <div className="flex items-start gap-3">
          <span
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-base font-semibold uppercase text-primary"
            aria-hidden="true"
          >
            {view.client.host.charAt(0)}
          </span>
          <div className="min-w-0">
            {/* The headline names the VERIFIED host. The document's own name is
                only a claim, so it shows second and in quotes (fix round 1). */}
            <h1 className="text-base font-semibold text-foreground">
              Connect <span className="break-all">{view.client.host}</span> to Metorite?
            </h1>
            {view.client.name && (
              <p className="mt-0.5 text-xs text-muted-foreground" data-testid="consent-claim">
                It calls itself: <span className="break-words">&ldquo;{view.client.name}&rdquo;</span>
              </p>
            )}
            <p className="mt-1 text-sm text-muted-foreground">
              It acts as you, and never sees more than you can.
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Badge tone="success" icon="ShieldCheck" size="md" title={view.client.origin}>
            <span data-testid="consent-origin" className="break-all">
              {view.client.host} verified
            </span>
          </Badge>
          <Badge tone="neutral" size="md" title={view.redirect_uri}>
            <span data-testid="consent-redirect" className="break-all">
              Returns to {view.redirect_app ? "an app on this computer" : view.redirect_display}
            </span>
          </Badge>
          <Badge tone="neutral" size="md">
            <span data-testid="consent-org">
              {orgName ? (
                <>Org {orgName}</>
              ) : accessLoading ? (
                <Skeleton className="inline-block h-3 w-24 align-middle" />
              ) : (
                <span className="text-warning">Metorite cannot name your organization. Reload the page.</span>
              )}
            </span>
          </Badge>
        </div>
      </div>

      <fieldset className="border-b border-border p-6">
        <legend className="sr-only">What the agent may do</legend>
        <h2 className="text-sm font-semibold text-foreground">What it may do</h2>
        <ul className="mt-3 space-y-3">
          {rows.map((row) => (
            <li key={row.scope} className="flex items-start justify-between gap-3">
              <div className="min-w-0 text-sm">
                <span className="block text-foreground">{row.label}</span>
                {row.detail && <span className="block text-xs text-muted-foreground">{row.detail}</span>}
              </div>
              <Switch label={row.label} checked={chosen.has(row.scope)} onChange={() => toggle(row.scope)} />
            </li>
          ))}
        </ul>
        {!canApprove(view, chosen) && (
          <p className="mt-3 text-xs text-warning">Choose one permission or more.</p>
        )}
      </fieldset>

      <fieldset className="border-b border-border p-6">
        <legend className="sr-only">Which projects</legend>
        <h2 className="text-sm font-semibold text-foreground">Which projects</h2>
        <Tabs
          chrome="inline"
          className="mt-3"
          tabs={[
            { id: "all", label: "All my projects" },
            { id: "pick", label: "Only the ones I pick" },
          ]}
          activeTab={mode}
          onTabChange={(id) => setMode(id as ProjectMode)}
        />
        {mode === "all" ? (
          <p className="mt-3 text-xs text-muted-foreground">
            Every team project you can see, now and later. Choose &ldquo;Only the ones I pick&rdquo; to limit it.
          </p>
        ) : (
          <div className="mt-3">
            {tree === null && !projectsFailed && (
              <div className="space-y-2 rounded-md border border-border p-3">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            )}
            {projectsFailed && (
              <p className="rounded-md border border-border p-3 text-xs text-muted-foreground">
                Metorite cannot list your projects now. Choose All my projects, or reload the page.
              </p>
            )}
            {tree !== null && <AccessTree tree={tree} selection={selection} onChange={setSelection} />}
            {projectIssue && tree !== null && (
              <p className="mt-2 text-xs text-warning" data-testid="consent-project-issue">
                {projectIssue}
              </p>
            )}
          </div>
        )}
        <div className="mt-4 flex items-start justify-between gap-3">
          <div className="min-w-0 text-sm">
            <span className="block text-foreground">My personal projects and notes</span>
            <span className="block text-xs text-muted-foreground">Off keeps them private from the agent.</span>
          </div>
          <Switch label="My personal projects and notes" checked={includePersonal} onChange={setIncludePersonal} />
        </div>
      </fieldset>

      <div className="flex flex-col gap-3 p-6">
        <p className="rounded-md bg-secondary/60 p-3 text-sm text-foreground" data-testid="consent-summary">
          {summary.map((part, i) =>
            part.strong ? (
              <strong key={i} className="font-semibold">
                {part.text}
              </strong>
            ) : (
              <span key={i}>{part.text}</span>
            ),
          )}
        </p>
        {problem && (
          <p className="text-sm text-warning" role="alert">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="secondary"
            disabled={sending}
            onClick={() => onLeave("You did not connect the agent.", view.deny_redirect_to)}
          >
            Deny
          </Button>
          <Button variant="primary" icon="Check" loading={sending} disabled={!ready} onClick={approve}>
            Approve
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Change or remove this access any time in <strong className="font-semibold">Integrations → AI agents</strong>.
        </p>
      </div>
    </Frame>
  );
}
