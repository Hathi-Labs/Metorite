"use client";

/**
 * The AI agents view of /integrations (WS-55 EA-4b, D-EA-9).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §5.4 and §15 EA-4b.
 * Every member sees it. Three parts:
 *
 * 1. **Connect an AI agent.** The MCP address with a copy button, one tab per
 *    client of §17.1, and what an agent can and cannot do.
 * 2. **My connected agents.** The member's own live connections, with Revoke.
 *    Above the list, the member's own switch "Let my AI agents delete tasks"
 *    (EA-6h, EA3-51), default off. The gateway checks it on each delete.
 * 3. **All agent connections.** Every live connection of the org, with the
 *    member's email and Revoke. Only with `integrations:manage`
 *    (`canManageOrg`), and the gateway checks it again.
 *
 * Each row headlines the VERIFIED host and shows the claimed name second, as
 * the consent page does (§10.5). `last_used_at` is stamped on a token issue
 * or refresh only, so the row calls it "Last token refresh".
 *
 * The decisions live in `../lib/agents.ts`, fenced by `agents.test.ts`. The
 * BFF rules are in `app/api/agent-access/connections/relay.ts`.
 */

import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Tabs from "@/components/Tabs";
import Badge, { type BadgeTone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { invalidate, put } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  AGENT_ACCESS_KEY,
  AGENT_CAN,
  AGENT_CANNOT,
  AGENT_CLIENTS,
  AGENTS_KEY_PREFIX,
  DELETE_SWITCH_COPY,
  MCP_URL,
  MY_AGENTS_KEY,
  ORG_AGENTS_KEY,
  agentClaim,
  agentHost,
  dayText,
  deleteSwitchHint,
  listMine,
  listOrg,
  personalText,
  pinsText,
  readAgentAccess,
  revoke,
  revokeCopy,
  saveAgentAccess,
  scopeWords,
  shownRows,
  type AgentConnection,
  type ClientState,
} from "../lib/agents";

const STATE_TONE: Record<ClientState, BadgeTone> = {
  tested: "success",
  untested: "warning",
  unsupported: "neutral",
};

/** Copy `text`, and say so in a toast. */
function useCopy(): (text: string, what: string) => void {
  const toast = useToast();
  return (text, what) => {
    void navigator.clipboard
      .writeText(text)
      .then(() => toast.show({ key: "agents:copy", variant: "success", title: `${what} copied` }))
      .catch(() =>
        toast.show({ key: "agents:copy", variant: "error", title: "Could not copy", description: text }),
      );
  };
}

// ── 1. The connect card ────────────────────────────────────────────────────

function ConnectCard() {
  const copy = useCopy();
  const [clientId, setClientId] = useState(AGENT_CLIENTS[0].id);
  const client = AGENT_CLIENTS.find((c) => c.id === clientId) ?? AGENT_CLIENTS[0];

  return (
    <section
      aria-labelledby="agents-connect-title"
      data-testid="agents-connect-card"
      className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4 sm:p-5"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon name="Bot" size={18} />
        </div>
        <div className="min-w-0">
          <h2 id="agents-connect-title" className="text-sm font-semibold text-foreground">
            Connect an AI agent
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            An outside AI agent, such as Claude Code, can work with your projects and tasks. It acts as you,
            and it sees only what you see.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">MCP server address</span>
        <div className="flex items-center gap-2">
          <code
            data-testid="agents-mcp-url"
            className="min-w-0 flex-1 truncate rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-xs text-foreground"
          >
            {MCP_URL}
          </code>
          <Button
            variant="secondary"
            size="sm"
            icon="Copy"
            onClick={() => copy(MCP_URL, "Address")}
            aria-label="Copy the MCP server address"
          >
            Copy
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <Tabs
          variant="underline"
          chrome="inline"
          tabs={AGENT_CLIENTS.map((c) => ({ id: c.id, label: c.label }))}
          activeTab={client.id}
          onTabChange={setClientId}
        />
        <div role="tabpanel" data-testid="agents-client-panel" className="flex flex-col gap-2">
          <div>
            <Badge tone={STATE_TONE[client.state]}>{client.stateLabel}</Badge>
          </div>
          <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm text-foreground">
            {client.steps.map((step) => (
              <li key={step.text}>
                <span>{step.text}</span>
                {step.code ? (
                  <div className="mt-1.5 flex items-center gap-2">
                    <code
                      data-testid="agents-client-command"
                      className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-xs"
                    >
                      {step.code}
                    </code>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon="Copy"
                      onClick={() => copy(step.code!, "Command")}
                      aria-label="Copy the command"
                    />
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      </div>

      <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold text-foreground">What an agent can do</h3>
          <ul className="mt-2 flex flex-col gap-1.5">
            {AGENT_CAN.map((line) => (
              <li key={line} className="flex items-start gap-2 text-xs text-muted-foreground">
                <Icon name="Check" size={14} className="mt-px shrink-0 text-success" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold text-foreground">What it cannot do</h3>
          <ul className="mt-2 flex flex-col gap-1.5">
            {AGENT_CANNOT.map((line) => (
              <li key={line} className="flex items-start gap-2 text-xs text-muted-foreground">
                <Icon name="X" size={14} className="mt-px shrink-0 text-destructive" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

// ── 2a. The delete switch (EA-6h) ──────────────────────────────────────────

function DeleteSwitchCard() {
  const toast = useToast();
  const access = useCachedResource(AGENT_ACCESS_KEY, readAgentAccess);
  const [busy, setBusy] = useState(false);

  // `null` is the 404 of the agent flag: no card at all.
  if (access.data === null) return null;
  const on = access.data?.delete_enabled === true;

  async function change(next: boolean): Promise<void> {
    setBusy(true);
    try {
      const stored = await saveAgentAccess(next);
      put(AGENT_ACCESS_KEY, stored);
      toast.show({
        key: "agents:delete-switch",
        variant: "success",
        title: stored.delete_enabled ? "Agents can delete tasks" : "Agents cannot delete tasks",
      });
    } catch (err) {
      toast.show({
        key: "agents:delete-switch",
        variant: "error",
        title: "The setting was not saved",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="agents-delete-switch-title"
      data-testid="agents-delete-switch"
      className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 sm:p-5"
    >
      {access.loading && access.data === undefined ? (
        <SkeletonRows count={1} />
      ) : access.data === undefined ? (
        <p className="text-xs text-muted-foreground">
          This setting did not load. {access.error ?? ""}
        </p>
      ) : (
        <label className="flex cursor-pointer items-start gap-3">
          <Checkbox
            checked={on}
            disabled={busy}
            onChange={(e) => void change(e.target.checked)}
            className="mt-0.5"
            aria-describedby="agents-delete-switch-body"
          />
          <span className="min-w-0">
            <span id="agents-delete-switch-title" className="block text-sm font-semibold text-foreground">
              {DELETE_SWITCH_COPY.title}
            </span>
            <span id="agents-delete-switch-body" className="mt-0.5 block text-xs text-muted-foreground">
              {DELETE_SWITCH_COPY.body}
            </span>
            <span data-testid="agents-delete-switch-state" className="mt-1.5 block text-xs text-foreground">
              {deleteSwitchHint(on)}
            </span>
          </span>
        </label>
      )}
    </section>
  );
}

// ── 2 and 3. A row of a list ───────────────────────────────────────────────

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="truncate text-xs text-foreground">{value}</dd>
    </div>
  );
}

function AgentRow({
  row,
  showMember,
  onRevoke,
}: {
  row: AgentConnection;
  showMember: boolean;
  onRevoke: (row: AgentConnection) => void;
}) {
  const host = agentHost(row);
  const claim = agentClaim(row);
  return (
    <li
      data-testid="agent-row"
      data-agent-id={row.id}
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-3 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          <Icon name="Bot" size={16} className="shrink-0 text-muted-foreground" />
          <span className="truncate text-sm font-medium text-foreground">{host}</span>
          {showMember ? (
            <span data-testid="agent-row-member" className="truncate text-xs text-muted-foreground">
              {row.member_email}
            </span>
          ) : null}
        </div>
        {claim ? <p className="text-xs text-muted-foreground">{claim}</p> : null}
        <div className="flex flex-wrap gap-1">
          {scopeWords(row.scopes).map((words) => (
            <Badge key={words} tone="neutral">
              {words}
            </Badge>
          ))}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3 lg:grid-cols-5">
          <Fact label="Projects" value={pinsText(row.pinned_projects)} />
          <Fact label="Personal projects" value={personalText(row.include_personal)} />
          <Fact label="Connected" value={dayText(row.created_at)} />
          <Fact label="Last token refresh" value={dayText(row.last_used_at)} />
          <Fact label="Expires" value={dayText(row.expires_at)} />
        </dl>
      </div>
      <Button
        variant="secondary"
        size="sm"
        icon="Unplug"
        className="self-start"
        onClick={() => onRevoke(row)}
        aria-label={`Revoke ${host}${showMember ? ` of ${row.member_email}` : ""}`}
      >
        Revoke
      </Button>
    </li>
  );
}

function AgentList({
  title,
  testId,
  rows,
  loading,
  error,
  off,
  showMember,
  empty,
  onRevoke,
}: {
  title: string;
  testId: string;
  rows: AgentConnection[];
  loading: boolean;
  error: string | null;
  off: boolean;
  showMember: boolean;
  empty: { message: string; hint: string };
  onRevoke: (row: AgentConnection) => void;
}) {
  const headingId = `${testId}-title`;
  return (
    <section aria-labelledby={headingId} data-testid={testId} className="flex flex-col gap-2">
      <h2 id={headingId} className="text-sm font-semibold text-foreground">
        {title}
      </h2>
      {loading ? (
        <SkeletonRows count={2} />
      ) : off ? (
        <EmptyState icon="PowerOff" message="Agent connections are off for this workspace." />
      ) : error && rows.length === 0 ? (
        <EmptyState icon="TriangleAlert" message="The list did not load." hint={error} />
      ) : rows.length === 0 ? (
        <EmptyState icon="Bot" message={empty.message} hint={empty.hint} />
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((row) => (
            <AgentRow key={row.id} row={row} showMember={showMember} onRevoke={onRevoke} />
          ))}
        </ul>
      )}
    </section>
  );
}

// ── The view ───────────────────────────────────────────────────────────────

export default function AgentsView({ canManageOrg }: { canManageOrg: boolean }) {
  const toast = useToast();
  const mine = useCachedResource(MY_AGENTS_KEY, listMine);
  const org = useCachedResource(canManageOrg ? ORG_AGENTS_KEY : null, listOrg);
  // Ids revoked here. Their rows go at once, before the lists read again.
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [asking, setAsking] = useState<{ row: AgentConnection; admin: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const off = mine.data === null;
  const copy = asking ? revokeCopy(asking.row, asking.admin) : null;

  async function confirmRevoke(): Promise<void> {
    if (!asking) return;
    const { row, admin } = asking;
    setBusy(true);
    try {
      const outcome = await revoke(row.id, admin);
      setGone((prev) => new Set(prev).add(row.id));
      setAsking(null);
      toast.show({
        key: `agents:revoke:${row.id}`,
        variant: "success",
        title: outcome === "revoked" ? "Agent revoked" : "That agent was already disconnected",
        description: agentHost(row),
      });
      invalidate(AGENTS_KEY_PREFIX);
    } catch (err) {
      toast.show({
        key: `agents:revoke:${row.id}`,
        variant: "error",
        title: "The agent was not revoked",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="agents-view" className="flex flex-1 flex-col gap-6 overflow-y-auto p-4 sm:p-6">
      {off ? null : <ConnectCard />}

      {off ? null : <DeleteSwitchCard />}

      <AgentList
        title="My connected agents"
        testId="agents-mine"
        rows={shownRows(mine.data, gone)}
        loading={mine.loading}
        error={mine.error}
        off={off}
        showMember={false}
        empty={{
          message: "You have not connected an agent yet.",
          hint: "Use the steps above to connect one.",
        }}
        onRevoke={(row) => setAsking({ row, admin: false })}
      />

      {canManageOrg && !off ? (
        <AgentList
          title="All agent connections"
          testId="agents-org"
          rows={shownRows(org.data, gone)}
          loading={org.loading}
          error={org.error}
          off={org.data === null}
          showMember
          empty={{
            message: "Nobody in your organization has connected an agent.",
            hint: "Each member connects their own agents.",
          }}
          onRevoke={(row) => setAsking({ row, admin: true })}
        />
      ) : null}

      <ConfirmDialog
        open={asking !== null}
        title={copy?.title ?? ""}
        subject={asking ? agentHost(asking.row) : null}
        body={copy?.body ?? ""}
        confirmLabel="Revoke"
        confirmVariant="destructive"
        icon="Unplug"
        busy={busy}
        onConfirm={() => void confirmRevoke()}
        onCancel={() => (busy ? undefined : setAsking(null))}
      />
    </div>
  );
}
