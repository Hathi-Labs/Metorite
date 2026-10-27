// Multi-inbox UI, item 4: the IMAP/SMTP connect form. Fence
// `email-imap-connect`. The route `POST /email/accounts` takes `credentials`
// with imap_host, imap_port, imap_username, imap_password, smtp_host and
// smtp_port as required keys (routes/email/transport/accounts.py), and the
// provider reads imap_use_ssl, smtp_username, smtp_password and
// smtp_use_starttls (email_ingestion/providers/imap.py).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  IMAP_DEFAULTS,
  imapCredentials,
  imapFormProblems,
  portForSmtpSecurity,
  type ImapForm,
} from "./imapConnect";

const read = (rel: string) =>
  readFileSync(join(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

const good: ImapForm = {
  ...IMAP_DEFAULTS,
  emailAddress: "me@example.test",
  label: "Work",
  imapHost: "imap.example.test",
  imapUsername: "me@example.test",
  imapPassword: "s3cret",
  smtpHost: "smtp.example.test",
};

describe("email-imap-connect", () => {
  it("builds exactly the credentials that the route and the provider read", () => {
    expect(imapCredentials(good)).toEqual({
      imap_host: "imap.example.test",
      imap_port: 993,
      imap_username: "me@example.test",
      imap_password: "s3cret",
      imap_use_ssl: true,
      smtp_host: "smtp.example.test",
      smtp_port: 587,
      smtp_use_starttls: true,
    });
  });

  it("sends a separate SMTP sign-in only when the member asks for one", () => {
    const own = imapCredentials({
      ...good,
      smtpSameLogin: false,
      smtpUsername: "send@example.test",
      smtpPassword: "other",
    });
    expect(own.smtp_username).toBe("send@example.test");
    expect(own.smtp_password).toBe("other");
    expect(imapCredentials(good)).not.toHaveProperty("smtp_username");
    expect(imapCredentials(good)).not.toHaveProperty("smtp_password");
  });

  it("maps SMTP security to the port the provider honours", () => {
    expect(portForSmtpSecurity("starttls")).toBe(587);
    expect(portForSmtpSecurity("ssl")).toBe(465);
    // The provider starts STARTTLS on port 587 only, and any other port gets SSL.
    expect(imapFormProblems({ ...good, smtpSecurity: "starttls", smtpPort: "2525" })).toContain(
      "STARTTLS works on port 587 only. Use port 587, or choose SSL/TLS.",
    );
    expect(imapCredentials({ ...good, smtpSecurity: "ssl", smtpPort: "465" })).toMatchObject({
      smtp_port: 465,
      smtp_use_starttls: false,
    });
  });

  it("names each problem in plain words and passes a good form", () => {
    expect(imapFormProblems(good)).toEqual([]);
    const bad = imapFormProblems({ ...good, emailAddress: "nope", imapHost: " ", imapPassword: "", imapPort: "0" });
    expect(bad).toEqual(
      expect.arrayContaining([
        "Enter the email address of the mailbox.",
        "Enter the IMAP server.",
        "Enter the password.",
        "Enter an IMAP port from 1 to 65535.",
      ]),
    );
  });

  it("is wired into the connect choices with a password field that autofill cannot cache", () => {
    const choices = read("components/ConnectChoices.tsx");
    expect(choices).toContain("ImapConnectForm");
    const form = read("components/ImapConnectForm.tsx");
    expect(form).toContain('type="password"');
    expect(form).toContain('autoComplete="new-password"');
    expect(form).toContain("createEmailAccount(");
    expect(form).not.toMatch(/console\.|localStorage|sessionStorage/);
    expect(form).toContain('from "@/components/ui/Input"');
    expect(form).toContain('from "@/components/ui/Button"');
  });

  it("binds every label to a control, and keeps the password after a failed connect", () => {
    const form = read("components/ImapConnectForm.tsx");
    const labels = [...form.matchAll(/htmlFor="([^"]+)"/g)].map((m) => m[1]);
    expect(labels).toContain("smtp-security");
    for (const id of labels) expect(form, `no control has id ${id}`).toContain(`id="${id}"`);
    // The secrets clear only once the account is saved, after the await.
    const cleared = form.indexOf('imapPassword: ""');
    expect(cleared).toBeGreaterThan(form.indexOf("await createEmailAccount("));
    expect(form.slice(form.indexOf("} catch (err)"))).not.toContain('imapPassword: ""');
  });
});
