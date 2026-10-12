"use client";

/**
 * The "MCP server" form of the Add menu (spec §3.4).
 *
 * It posts to the existing `POST /api/integrations/mcp`. The auth header value
 * is write-only: the field is a password field, the state is dropped when the
 * form closes, and nothing in the console draws it again. A refusal from the
 * server's URL guard (a private address, a non-https URL) shows as the server
 * wrote it.
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import * as api from "../lib/api";
import {
  ALWAYS_CONFIRM_HELP,
  ALWAYS_CONFIRM_LABEL,
  EMPTY_MCP_FORM,
  mcpBody,
  mcpFormReady,
  type McpForm,
} from "../lib/connections";

/**
 * The H-300 switch: "Always ask before any tool runs". One labelled tick on
 * the shared Checkbox, with the help line under it. The Add form and the side
 * panel both draw this one control.
 */
export function AlwaysConfirmField({
  checked,
  disabled,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  const id = useId();
  const helpId = `${id}-help`;
  return (
    <div className="flex items-start gap-2">
      <Checkbox
        id={id}
        className="mt-0.5"
        checked={checked}
        disabled={disabled}
        aria-describedby={helpId}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div className="flex min-w-0 flex-col gap-0.5">
        <label htmlFor={id} className="text-sm text-foreground">
          {ALWAYS_CONFIRM_LABEL}
        </label>
        <span id={helpId} className="text-[11px] text-muted-foreground">
          {ALWAYS_CONFIRM_HELP}
        </span>
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
      {hint ? <span className="text-[11px] text-muted-foreground">{hint}</span> : null}
    </label>
  );
}

export function McpFormFields({
  form,
  onChange,
}: {
  form: McpForm;
  onChange: (next: McpForm) => void;
}) {
  const set = (key: keyof McpForm) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ ...form, [key]: e.target.value });
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name">
          <Input value={form.name} onChange={set("name")} placeholder="brave-search" />
        </Field>
        <Field label="Label">
          <Input value={form.label} onChange={set("label")} placeholder="Brave Search" />
        </Field>
      </div>
      <Field label="Description (optional)">
        <Input value={form.description} onChange={set("description")} placeholder="What the server offers" />
      </Field>
      <Field label="URL" hint="Use an https address. Private and local addresses are refused.">
        <Input
          value={form.url}
          onChange={set("url")}
          placeholder="https://mcp.example.com/sse"
          inputMode="url"
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Auth header name (optional)">
          <Input value={form.headerName} onChange={set("headerName")} placeholder="Authorization" />
        </Field>
        <Field label="Auth header value" hint="Write-only. It is never shown again.">
          <Input
            type="password"
            autoComplete="off"
            value={form.headerValue}
            onChange={set("headerValue")}
            placeholder="Bearer …"
          />
        </Field>
      </div>
      <Field label="Agent scope" hint="Agent names, separated by commas. Use * for all agents.">
        <Input value={form.agentScope} onChange={set("agentScope")} />
      </Field>
      <AlwaysConfirmField
        checked={form.alwaysConfirm}
        onChange={(next) => onChange({ ...form, alwaysConfirm: next })}
      />
    </div>
  );
}

export default function AddMcpModal({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const [form, setForm] = useState<McpForm>(EMPTY_MCP_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.addMcp(mcpBody(form));
      // The header value leaves memory with the form.
      setForm(EMPTY_MCP_FORM);
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The request failed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Add an MCP server"
      description="Agents can use its tools after you add it."
      size="md"
      closeLabel="Close"
    >
      <div className="flex flex-col gap-4 p-4">
        <McpFormFields form={form} onChange={setForm} />
        {error ? (
          <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" loading={saving} disabled={!mcpFormReady(form)} onClick={save}>
            Add server
          </Button>
        </div>
      </div>
    </Modal>
  );
}
