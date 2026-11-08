"use client";

/**
 * Box tokens in Integrations, AI agents (WS-55 EA-19d, spec §10.9 questions
 * 3, 4 and 5). The pieces that `AgentsView.tsx` places:
 *
 * - `ConnectBoxDialog`: the form, then the token ONCE with the connect command.
 * - `BoxTokenModal`: the same one-time panel for a Rotate and a Rotate now.
 * - `BoxMarks`: the Box badge, the expiry chip and the new-address mark of a row.
 * - `RevokeAllBoxes`: the admin button, with its confirm dialog.
 *
 * ⚠️ The raw token lives in the state of one inner component, which unmounts
 * when the dialog closes. It is in no URL, no storage, no log and no cached
 * list. The decisions and the requests are in `../lib/box.ts`.
 */

import { useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { Switch } from "@/components/ui/Switch";
import { useToast } from "@/components/ui/Toast";
import { statusAccent } from "@/lib/statusAccent";

import {
  BOX_CONNECT_COMMAND,
  BOX_CONNECT_HINT,
  BOX_DAYS_DEFAULT,
  BOX_DAYS_MAX,
  BOX_NAME_MAX,
  BOX_OFF_MESSAGE,
  BOX_TOKEN_WARNING,
  EMPTY_BOX_FORM,
  boxScopeRows,
  daysProblem,
  expiryView,
  formProblem,
  isBox,
  mintBoxToken,
  nameProblem,
  newAddress,
  revokeAllBoxTokens,
  revokeAllCopy,
  revokedText,
  toggleBoxScope,
  type BoxForm,
  type IssuedBoxToken,
} from "../lib/box";
import type { AgentConnection } from "../lib/agents";

function useCopy(): (text: string, what: string) => void {
  const toast = useToast();
  return (text, what) => {
    void navigator.clipboard
      .writeText(text)
      .then(() => toast.show({ key: "agents:box-copy", variant: "success", title: `${what} copied` }))
      .catch(() => toast.show({ key: "agents:box-copy", variant: "error", title: "Could not copy" }));
  };
}

// ── The row marks ──────────────────────────────────────────────────────────

/** The Box badge, and the expiry chip, beside the host of a box row. */
export function BoxBadges({ row, now }: { row: AgentConnection; now?: number }) {
  if (!isBox(row)) return null;
  const expiry = expiryView(row.expires_at, now);
  const accent = expiry.color ? statusAccent({ color: expiry.color }) : null;
  return (
    <>
      <span data-testid="box-badge">
        <Badge tone="info" icon="Server">
          Box
        </Badge>
      </span>
      {accent ? (
        <span
          data-testid="box-expiry"
          data-expiry-color={expiry.color}
          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${accent.chip}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${accent.dot}`} aria-hidden />
          {expiry.text}
        </span>
      ) : null}
    </>
  );
}

/** The new-address mark: a warning pill with the address, and what to do. */
export function NewAddressMark({ row, now }: { row: AgentConnection; now?: number }) {
  const mark = newAddress(row, now);
  if (!mark) return null;
  const accent = statusAccent({ color: "amber" });
  return (
    <div data-testid="box-new-address" className="flex flex-col gap-1">
      <span
        className={`inline-flex w-fit max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${accent.chip}`}
      >
        <Icon name="TriangleAlert" size={12} className="shrink-0" />
        <span className="truncate">New address: {mark.addr}</span>
      </span>
      <p className="text-xs text-muted-foreground">{mark.hint}</p>
    </div>
  );
}

// ── The one-time panel ─────────────────────────────────────────────────────

/** The token, once, with Copy, the warning and the connect command. */
export function BoxTokenPanel({ issued, onDone }: { issued: IssuedBoxToken; onDone: () => void }) {
  const copy = useCopy();
  return (
    <div data-testid="box-token-panel" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">Token for {issued.name}</span>
        <div className="flex items-center gap-2">
          <Input
            readOnly
            value={issued.token}
            aria-label="Box token"
            data-testid="box-token-value"
            autoComplete="off"
            spellCheck={false}
            onFocus={(e) => e.currentTarget.select()}
            className="min-w-0 flex-1 font-mono"
          />
          <Button
            variant="secondary"
            size="sm"
            icon="Copy"
            onClick={() => copy(issued.token, "Token")}
            aria-label="Copy the token"
          >
            Copy
          </Button>
        </div>
        <p data-testid="box-token-warning" className="flex items-start gap-1.5 text-xs text-warning">
          <Icon name="TriangleAlert" size={14} className="mt-px shrink-0" />
          <span>{BOX_TOKEN_WARNING}</span>
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">Connect the box</span>
        <div className="flex items-center gap-2">
          <code
            data-testid="box-connect-command"
            className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-xs"
          >
            {BOX_CONNECT_COMMAND}
          </code>
          <Button
            variant="ghost"
            size="icon-sm"
            icon="Copy"
            onClick={() => copy(BOX_CONNECT_COMMAND, "Command")}
            aria-label="Copy the command"
          />
        </div>
        <p className="text-xs text-muted-foreground">{BOX_CONNECT_HINT}</p>
      </div>
      <div className="flex justify-end">
        <Button variant="primary" size="sm" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}

/** The panel in a dialog, for a rotation. `issued` null means closed. */
export function BoxTokenModal({ issued, onClose }: { issued: IssuedBoxToken | null; onClose: () => void }) {
  return (
    <Modal open={issued !== null} onClose={onClose} title="New box token" icon="KeyRound" size="lg">
      <div className="p-4">{issued ? <BoxTokenPanel issued={issued} onDone={onClose} /> : null}</div>
    </Modal>
  );
}

// ── The connect dialog ─────────────────────────────────────────────────────

export function ConnectForm({ onClose, onMade }: { onClose: () => void; onMade: () => void }) {
  const [form, setForm] = useState<BoxForm>(EMPTY_BOX_FORM);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The raw token. It lives here only, and it goes when this component unmounts.
  const [issued, setIssued] = useState<IssuedBoxToken | null>(null);

  if (issued) return <BoxTokenPanel issued={issued} onDone={onClose} />;

  const nameError = touched ? nameProblem(form.name) : null;
  const daysError = daysProblem(form.days);

  async function submit(): Promise<void> {
    setTouched(true);
    if (formProblem(form)) return;
    setBusy(true);
    setError(null);
    const result = await mintBoxToken(form);
    setBusy(false);
    if (result.ok) {
      setIssued(result.value);
      onMade();
    } else if (result.off) {
      setOff(true);
    } else {
      setError(result.message);
    }
  }

  if (off) {
    return (
      <div className="flex flex-col gap-4">
        <p data-testid="box-off" className="text-sm text-foreground">
          {BOX_OFF_MESSAGE}
        </p>
        <div className="flex justify-end">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form
      data-testid="box-form"
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">Name of the box</span>
        <Input
          value={form.name}
          maxLength={BOX_NAME_MAX}
          placeholder="metorite-home"
          aria-invalid={nameError ? true : undefined}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          onBlur={() => setTouched(true)}
          autoFocus
        />
        {nameError ? (
          <span data-testid="box-name-error" className="text-xs text-destructive">
            {nameError}
          </span>
        ) : null}
      </label>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-xs font-medium text-muted-foreground">What the box may do</legend>
        {boxScopeRows().map((row) => (
          <label key={row.scope} className="flex items-start gap-3">
            <Switch
              checked={form.scopes.includes(row.scope)}
              onChange={() => setForm({ ...form, scopes: toggleBoxScope(form.scopes, row.scope) })}
            />
            <span className="min-w-0">
              <span className="block text-sm text-foreground">{row.label}</span>
              <span className="block text-xs text-muted-foreground">{row.detail}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <label className="flex items-start gap-3">
        <Switch
          checked={form.includePersonal}
          onChange={(next) => setForm({ ...form, includePersonal: next })}
        />
        <span className="min-w-0">
          <span className="block text-sm text-foreground">Personal projects</span>
          <span className="block text-xs text-muted-foreground">
            Let the box see your personal projects. Off by default.
          </span>
        </span>
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">Days until the token expires</span>
        <Input
          type="number"
          min={1}
          max={BOX_DAYS_MAX}
          value={Number.isFinite(form.days) ? form.days : ""}
          aria-invalid={daysError ? true : undefined}
          onChange={(e) => setForm({ ...form, days: e.target.value === "" ? NaN : Number(e.target.value) })}
          className="w-28"
        />
        <span className={`text-xs ${daysError ? "text-destructive" : "text-muted-foreground"}`}>
          {daysError ?? `${BOX_DAYS_DEFAULT} days is the usual choice. The most is ${BOX_DAYS_MAX}.`}
        </span>
      </label>

      {error ? (
        <p data-testid="box-error" role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}

      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm" type="button" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={busy}>
          Make token
        </Button>
      </div>
    </form>
  );
}

/** The "Connect a box" button and its dialog. */
export function ConnectBoxButton({ onMade }: { onMade: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" size="sm" icon="Server" onClick={() => setOpen(true)}>
        Connect a box
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Connect a box" icon="Server" size="lg">
        {/* Mounted only while open, so the token leaves state on close. */}
        {open ? (
          <div className="p-4">
            <ConnectForm onClose={() => setOpen(false)} onMade={onMade} />
          </div>
        ) : null}
      </Modal>
    </>
  );
}

// ── Revoke all (admin) ─────────────────────────────────────────────────────

/** "Revoke all box tokens". The caller shows it to an admin only. */
export function RevokeAllBoxes({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const copy = revokeAllCopy();

  async function confirm(): Promise<void> {
    setBusy(true);
    const result = await revokeAllBoxTokens();
    setBusy(false);
    setAsking(false);
    if (result.ok) {
      toast.show({ key: "agents:box-revoke-all", variant: "success", title: revokedText(result.value) });
      onDone();
    } else {
      toast.show({
        key: "agents:box-revoke-all",
        variant: "error",
        title: "The box tokens were not revoked",
        description: result.message,
      });
    }
  }

  return (
    <>
      <Button variant="secondary" size="sm" icon="Unplug" onClick={() => setAsking(true)}>
        Revoke all box tokens
      </Button>
      <ConfirmDialog
        open={asking}
        title={copy.title}
        body={copy.body}
        confirmLabel="Revoke all"
        confirmVariant="destructive"
        icon="Unplug"
        busy={busy}
        onConfirm={() => void confirm()}
        onCancel={() => (busy ? undefined : setAsking(false))}
      />
    </>
  );
}
