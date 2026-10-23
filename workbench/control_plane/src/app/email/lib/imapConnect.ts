/**
 * The IMAP/SMTP connect form, as decisions (multi-inbox UI, item 4).
 *
 * `POST /email/accounts` takes `{provider: "imap", email_address, label,
 * credentials}`. The route requires `imap_host`, `imap_port`,
 * `imap_username`, `imap_password`, `smtp_host` and `smtp_port` in
 * `credentials`. The provider (`email_ingestion/providers/imap.py`) also reads
 * `imap_use_ssl`, `smtp_username`, `smtp_password` and `smtp_use_starttls`.
 *
 * The provider starts STARTTLS on port 587 only, and any other SMTP port gets
 * SSL/TLS. So the form offers two SMTP choices and checks the port against
 * them. The form offers SSL/TLS for IMAP only: a plain IMAP login sends the
 * password in the clear, and the form does not ask the member to choose that.
 *
 * Nothing here stores or logs a value. Fence: `imapConnect.test.ts`.
 */

export type SmtpSecurity = "starttls" | "ssl";

/** The text state of the form. Ports stay text until the form is checked. */
export interface ImapForm {
  emailAddress: string;
  label: string;
  imapHost: string;
  imapPort: string;
  imapUsername: string;
  imapPassword: string;
  smtpHost: string;
  smtpPort: string;
  smtpSecurity: SmtpSecurity;
  /** True: SMTP uses the IMAP username and password. */
  smtpSameLogin: boolean;
  smtpUsername: string;
  smtpPassword: string;
}

export const IMAP_DEFAULTS: ImapForm = {
  emailAddress: "",
  label: "",
  imapHost: "",
  imapPort: "993",
  imapUsername: "",
  imapPassword: "",
  smtpHost: "",
  smtpPort: "587",
  smtpSecurity: "starttls",
  smtpSameLogin: true,
  smtpUsername: "",
  smtpPassword: "",
};

/** The usual port of each SMTP security choice. */
export function portForSmtpSecurity(security: SmtpSecurity): number {
  return security === "starttls" ? 587 : 465;
}

function port(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= 1 && n <= 65535 ? n : null;
}

const ADDRESS = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Each problem of the form, in plain words. Empty when the form is good. */
export function imapFormProblems(f: ImapForm): string[] {
  const out: string[] = [];
  if (!ADDRESS.test(f.emailAddress.trim())) out.push("Enter the email address of the mailbox.");
  if (!f.imapHost.trim()) out.push("Enter the IMAP server.");
  if (port(f.imapPort) === null) out.push("Enter an IMAP port from 1 to 65535.");
  if (!f.imapUsername.trim()) out.push("Enter the username.");
  if (!f.imapPassword) out.push("Enter the password.");
  if (!f.smtpHost.trim()) out.push("Enter the SMTP server.");
  const smtp = port(f.smtpPort);
  if (smtp === null) out.push("Enter an SMTP port from 1 to 65535.");
  else if (f.smtpSecurity === "starttls" && smtp !== 587) {
    out.push("STARTTLS works on port 587 only. Use port 587, or choose SSL/TLS.");
  }
  if (!f.smtpSameLogin) {
    if (!f.smtpUsername.trim()) out.push("Enter the SMTP username.");
    if (!f.smtpPassword) out.push("Enter the SMTP password.");
  }
  return out;
}

/**
 * The `credentials` object of the route. Call it only for a form with no
 * problem. The SMTP sign-in keys are absent when SMTP uses the IMAP sign-in,
 * and the provider then falls back to the IMAP values.
 */
export function imapCredentials(f: ImapForm): Record<string, unknown> {
  const credentials: Record<string, unknown> = {
    imap_host: f.imapHost.trim(),
    imap_port: Number(f.imapPort.trim()),
    imap_username: f.imapUsername.trim(),
    imap_password: f.imapPassword,
    imap_use_ssl: true,
    smtp_host: f.smtpHost.trim(),
    smtp_port: Number(f.smtpPort.trim()),
    smtp_use_starttls: f.smtpSecurity === "starttls",
  };
  if (!f.smtpSameLogin) {
    credentials.smtp_username = f.smtpUsername.trim();
    credentials.smtp_password = f.smtpPassword;
  }
  return credentials;
}
