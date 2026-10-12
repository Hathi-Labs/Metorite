"use client";

/**
 * The side panel of one connection. It opens as a `Modal` drawer at the right
 * edge, as the CRM record sheet does.
 *
 * The panel shows every `detail` field and never a stored value (spec §3.3
 * rule 4). Setup belongs to the owning app, so "Open in <app>" and "Connect
 * again" are links. Only Disconnect, Test and the "always confirm" switch of
 * an MCP server (H-300) call the console's own routes.
 */

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { useViewMode } from "@/components/ViewModeProvider";

import {
  APP_LABEL,
  detailRows,
  errorMessage,
  lastSyncLabel,
  mcpAlwaysConfirm,
  panelPlan,
  rowLabel,
  scopeLabel,
  type Connection,
} from "../lib/connections";
import { AlwaysConfirmField } from "./AddMcpModal";
import { ConnectionState } from "./ConnectionList";

export interface SheetBodyProps {
  row: Connection;
  canManage: boolean;
  busy?: boolean;
  testing?: boolean;
  now?: number;
  onNavigate: (href: string) => void;
  onDisconnect: () => void;
  onTest: () => void;
  /** H-300: the admin turns the "always confirm" switch of an MCP server. */
  onAlwaysConfirm?: (next: boolean) => void;
  /** True while that change is on its way. */
  savingAlwaysConfirm?: boolean;
}

export function SheetBody({
  row,
  canManage,
  busy,
  testing,
  now,
  onNavigate,
  onDisconnect,
  onTest,
  onAlwaysConfirm,
  savingAlwaysConfirm,
}: SheetBodyProps) {
  const plan = panelPlan(row, canManage);
  const error = errorMessage(row.last_error);
  const fields = [
    { label: "App", value: APP_LABEL[row.owning_app] },
    { label: "Scope", value: scopeLabel(row) },
    { label: "Last sync", value: lastSyncLabel(row.last_sync_at, now) },
    ...detailRows(row),
  ];
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
      <div className="flex items-center gap-2">
        <ConnectionState status={row.status} />
        <span className="text-xs text-muted-foreground">{row.provider}</span>
      </div>
      {error ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 text-sm">
        {fields.map((f) => (
          <div key={f.label} className="contents">
            <dt className="text-xs text-muted-foreground">{f.label}</dt>
            <dd className="min-w-0 break-words text-foreground">{f.value}</dd>
          </div>
        ))}
      </dl>
      {plan.alwaysConfirm ? (
        <AlwaysConfirmField
          checked={mcpAlwaysConfirm(row) === true}
          disabled={plan.alwaysConfirm !== "edit" || !onAlwaysConfirm || savingAlwaysConfirm}
          onChange={(next) => onAlwaysConfirm?.(next)}
        />
      ) : null}
      {plan.memberNote ? (
        <p className="rounded-lg bg-secondary px-3 py-2 text-xs text-muted-foreground">{plan.memberNote}</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {plan.reconnect && plan.reconnectHref ? (
          <Button variant="primary" size="sm" icon="RefreshCw" onClick={() => onNavigate(plan.reconnectHref!)}>
            Connect again
          </Button>
        ) : null}
        {plan.openLabel && plan.href ? (
          <Button variant="secondary" size="sm" icon="ExternalLink" onClick={() => onNavigate(plan.href!)}>
            {plan.openLabel}
          </Button>
        ) : null}
        {plan.test ? (
          <Button variant="secondary" size="sm" icon="Activity" loading={testing} onClick={onTest}>
            Test
          </Button>
        ) : null}
        {plan.disconnect ? (
          <Button variant="destructive" size="sm" icon="Unplug" loading={busy} onClick={onDisconnect}>
            Disconnect
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export default function ConnectionSheet(props: SheetBodyProps & { onClose: () => void }) {
  const { isMobile } = useViewMode();
  const { onClose, ...body } = props;
  return (
    <Modal
      open
      onClose={onClose}
      title={rowLabel(body.row)}
      description={APP_LABEL[body.row.owning_app]}
      size="md"
      placement={isMobile ? "sheet" : "end"}
      closeLabel="Close connection"
      className="h-full"
    >
      <SheetBody {...body} />
    </Modal>
  );
}
