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
import { Checkbox } from "@/components/ui/Checkbox";
import { Skeleton } from "@/components/ui/Skeleton";

import {
  type AuthorizeRequest,
  type CheckView,
  type ParsedRequest,
  type PinChoice,
  type Refusal,
  approveBody,
  canApprove,
  gateOpen,
  gateStart,
  gateWaitMs,
  initialChoice,
  isCheckView,
  organizationName,
  pinChoices,
  refusalFor,
  sameScreen,
  scopeRows,
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
  const rows = useMemo(() => scopeRows(view.scopes), [view.scopes]);
  const [chosen, setChosen] = useState<Set<string>>(() => initialChoice(view));
  const [pins, setPins] = useState<string[]>([]);
  const [includePersonal, setIncludePersonal] = useState(false);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [projects, setProjects] = useState<PinChoice[] | null>(null);
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
        setProjects(pinChoices(await res.json()));
      })
      .catch(() => {
        if (!controller.signal.aborted) setProjectsFailed(true);
      });
    return () => controller.abort();
  }, []);

  const toggle = (scope: string) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  const togglePin = (id: string) =>
    setPins((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]));

  const ready = canApprove(view, chosen) && !!orgName && !sending && gate.open;

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
        approveBody(request, ticketView, { scopes: chosen, pins, includePersonal }),
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
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Icon name="Bot" size={20} />
          </span>
          <div className="min-w-0">
            {/* The headline names the VERIFIED host. The document's own name is
                only a claim, so it shows second and in quotes (fix round 1). */}
            <h1 className="text-base font-semibold text-foreground">
              <span className="break-all">{view.client.host}</span> wants to connect to Metorite
            </h1>
            {view.client.name && (
              <p className="mt-0.5 text-xs text-muted-foreground" data-testid="consent-claim">
                It calls itself: <span className="break-words">&ldquo;{view.client.name}&rdquo;</span>
              </p>
            )}
            <p className="mt-1 text-sm text-muted-foreground">
              The agent will act as you. It can never see or change more than you can.
            </p>
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-muted-foreground">Verified site</dt>
          <dd className="flex min-w-0 items-center gap-1.5 text-foreground" data-testid="consent-origin">
            <Icon name="ShieldCheck" size={14} className="shrink-0 text-success" />
            <span className="break-all font-mono text-xs">{view.client.origin}</span>
          </dd>
          <dt className="text-muted-foreground">Returns to</dt>
          <dd className="min-w-0 text-foreground" data-testid="consent-redirect">
            <span className="break-all font-mono text-xs">{view.redirect_display}</span>
            {view.redirect_app && (
              <span className="ml-1.5 text-xs text-muted-foreground">an app on this computer</span>
            )}
          </dd>
          <dt className="text-muted-foreground">Organization</dt>
          <dd className="min-w-0 text-foreground" data-testid="consent-org">
            {orgName ? (
              <span className="break-words">{orgName}</span>
            ) : accessLoading ? (
              <Skeleton className="h-4 w-32" />
            ) : (
              <span className="text-warning">Metorite cannot name your organization. Reload the page.</span>
            )}
          </dd>
        </dl>
      </div>

      <fieldset className="border-b border-border p-6">
        <legend className="sr-only">What the agent may do</legend>
        <h2 className="text-sm font-semibold text-foreground">What the agent may do</h2>
        <ul className="mt-3 space-y-3">
          {rows.map((row) => (
            <li key={row.scope} className="flex items-start gap-2.5">
              {row.fixed ? (
                <Icon name="Lock" size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
              ) : (
                <Checkbox
                  id={`scope-${row.scope}`}
                  className="mt-0.5"
                  checked={chosen.has(row.scope)}
                  onChange={() => toggle(row.scope)}
                />
              )}
              <label htmlFor={row.fixed ? undefined : `scope-${row.scope}`} className="min-w-0 text-sm">
                <span className="block text-foreground">{row.label}</span>
                {row.detail && <span className="block text-xs text-muted-foreground">{row.detail}</span>}
              </label>
            </li>
          ))}
        </ul>
        {!canApprove(view, chosen) && (
          <p className="mt-3 text-xs text-warning">Choose one permission or more.</p>
        )}
      </fieldset>

      <fieldset className="border-b border-border p-6">
        <legend className="sr-only">Projects</legend>
        <h2 className="text-sm font-semibold text-foreground">Projects</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {pins.length === 0
            ? "Every project you can see. Pick projects to limit the agent to them and their subprojects."
            : `Only ${pins.length} pinned ${pins.length === 1 ? "project" : "projects"} and their subprojects.`}
        </p>
        <div className="mt-3 max-h-48 overflow-auto rounded-md border border-border" data-testid="consent-pins">
          {projects === null && !projectsFailed && (
            <div className="space-y-2 p-3">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          )}
          {projectsFailed && (
            <p className="p-3 text-xs text-muted-foreground">Metorite cannot list your projects now.</p>
          )}
          {projects !== null && projects.length === 0 && (
            <p className="p-3 text-xs text-muted-foreground">You see no team projects.</p>
          )}
          {projects !== null && projects.length > 0 && (
            <ul className="py-1">
              {projects.map((p) => (
                <li key={p.id}>
                  <label
                    className="flex cursor-pointer items-center gap-2 px-3 py-1 text-sm text-foreground hover:bg-secondary"
                    style={{ paddingLeft: `${0.75 + p.depth * 1}rem` }}
                  >
                    <Checkbox size="sm" checked={pins.includes(p.id)} onChange={() => togglePin(p.id)} />
                    <span className="truncate">{p.name}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
        <label className="mt-4 flex cursor-pointer items-start gap-2.5 text-sm">
          <Checkbox
            className="mt-0.5"
            checked={includePersonal}
            onChange={(e) => setIncludePersonal(e.target.checked)}
          />
          <span className="min-w-0">
            <span className="block text-foreground">Include my personal projects</span>
            <span className="block text-xs text-muted-foreground">
              Off by default. Your personal projects stay private unless you tick this.
            </span>
          </span>
        </label>
      </fieldset>

      <div className="flex flex-col gap-3 p-6">
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
          Approve only an agent you trust. To disconnect it later, remove it in the agent, or ask an admin.
        </p>
      </div>
    </Frame>
  );
}
