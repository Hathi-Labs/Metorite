"use client";

/**
 * The IMAP/SMTP connect form (multi-inbox UI, item 4). It posts to
 * `POST /email/accounts` through `createEmailAccount`, which the gateway
 * allows for IMAP only. The decisions are in `lib/imapConnect.ts`.
 *
 * The password lives in this component's state and nowhere else. The form
 * never logs it, stores it or puts it in a URL, and it clears every secret
 * field once the gateway answers. The gateway encrypts it at rest.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import Input from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { createEmailAccount } from "../lib/api";
import { useEmailStore } from "../lib/emailStore";
import {
  IMAP_DEFAULTS,
  imapCredentials,
  imapFormProblems,
  portForSmtpSecurity,
  type ImapForm,
  type SmtpSecurity,
} from "../lib/imapConnect";

function Row({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-xs font-medium text-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

export function ImapConnectForm({
  onBack,
  onDone,
}: {
  onBack: () => void;
  /** Called once the mailbox is connected and selected. */
  onDone?: () => void;
}) {
  const [form, setForm] = useState<ImapForm>(IMAP_DEFAULTS);
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof ImapForm>(key: K, value: ImapForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const setSecurity = (security: SmtpSecurity) =>
    setForm((f) => ({ ...f, smtpSecurity: security, smtpPort: String(portForSmtpSecurity(security)) }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found = imapFormProblems(form);
    setProblems(found);
    if (found.length > 0) return;
    setBusy(true);
    setError(null);
    try {
      const account = await createEmailAccount({
        provider: "imap",
        emailAddress: form.emailAddress.trim(),
        label: form.label.trim(),
        credentials: imapCredentials(form),
      });
      // Clear every secret at once. The mailbox is saved on the gateway.
      setForm((f) => ({ ...f, imapPassword: "", smtpPassword: "" }));
      await useEmailStore.getState().fetchAccounts();
      useEmailStore.getState().selectAccount(account.id);
      onDone?.();
    } catch (err) {
      setError(
        (err as Error).message || "Metorite could not connect this mailbox. Check the details and try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
      <div>
        <h3 className="text-sm font-semibold text-foreground">Connect with IMAP and SMTP</h3>
        <p className="text-xs text-muted-foreground">
          Use this for a mailbox that is not on Microsoft 365 or Google. Metorite stores the password
          encrypted.
        </p>
      </div>
      <Row label="Email address" htmlFor="imap-address">
        <Input
          id="imap-address"
          type="email"
          autoComplete="off"
          value={form.emailAddress}
          onChange={(e) => set("emailAddress", e.target.value)}
        />
      </Row>
      <Row label="Name (optional)" htmlFor="imap-label">
        <Input id="imap-label" autoComplete="off" value={form.label} onChange={(e) => set("label", e.target.value)} />
      </Row>
      <div className="grid grid-cols-[1fr_6rem] gap-2">
        <Row label="IMAP server" htmlFor="imap-host">
          <Input id="imap-host" autoComplete="off" value={form.imapHost} onChange={(e) => set("imapHost", e.target.value)} />
        </Row>
        <Row label="Port" htmlFor="imap-port">
          <Input id="imap-port" inputMode="numeric" value={form.imapPort} onChange={(e) => set("imapPort", e.target.value)} />
        </Row>
      </div>
      <p className="-mt-1 text-[11px] text-muted-foreground">IMAP uses SSL/TLS.</p>
      <Row label="Username" htmlFor="imap-user">
        <Input id="imap-user" autoComplete="off" value={form.imapUsername} onChange={(e) => set("imapUsername", e.target.value)} />
      </Row>
      <Row label="Password" htmlFor="imap-password">
        <Input
          id="imap-password"
          type="password"
          autoComplete="new-password"
          value={form.imapPassword}
          onChange={(e) => set("imapPassword", e.target.value)}
        />
      </Row>
      <div className="grid grid-cols-[1fr_6rem] gap-2">
        <Row label="SMTP server" htmlFor="smtp-host">
          <Input id="smtp-host" autoComplete="off" value={form.smtpHost} onChange={(e) => set("smtpHost", e.target.value)} />
        </Row>
        <Row label="Port" htmlFor="smtp-port">
          <Input id="smtp-port" inputMode="numeric" value={form.smtpPort} onChange={(e) => set("smtpPort", e.target.value)} />
        </Row>
      </div>
      <Row label="SMTP security" htmlFor="smtp-security">
        <SelectButton
          id="smtp-security"
          label="SMTP security"
          value={form.smtpSecurity}
          options={[
            { value: "starttls", label: "STARTTLS (port 587)" },
            { value: "ssl", label: "SSL/TLS (port 465)" },
          ]}
          onChange={(v) => setSecurity(v as SmtpSecurity)}
          widthClass="w-full"
        />
      </Row>
      <label className="flex items-center gap-2 text-xs text-foreground">
        <Checkbox checked={form.smtpSameLogin} onChange={(e) => set("smtpSameLogin", e.target.checked)} />
        Send with the same username and password
      </label>
      {!form.smtpSameLogin && (
        <>
          <Row label="SMTP username" htmlFor="smtp-user">
            <Input id="smtp-user" autoComplete="off" value={form.smtpUsername} onChange={(e) => set("smtpUsername", e.target.value)} />
          </Row>
          <Row label="SMTP password" htmlFor="smtp-password">
            <Input
              id="smtp-password"
              type="password"
              autoComplete="new-password"
              value={form.smtpPassword}
              onChange={(e) => set("smtpPassword", e.target.value)}
            />
          </Row>
        </>
      )}
      {problems.length > 0 && (
        <ul role="alert" className="list-disc pl-5 text-xs text-destructive">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex justify-between gap-2">
        <Button type="button" variant="ghost" onClick={onBack} disabled={busy}>
          Back
        </Button>
        <Button type="submit" loading={busy}>
          Connect mailbox
        </Button>
      </div>
    </form>
  );
}
