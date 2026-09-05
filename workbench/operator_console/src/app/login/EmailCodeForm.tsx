"use client";

import { useState } from "react";

import { otpStartBody, otpStartUrl, otpVerifyBody } from "@/lib/otp";

// The EMAIL CODE sign-in — WS-31 CP-12j, **D71.3**.
//
// Two steps in one component. Ask Supabase to mail a six-digit code, then
// exchange the code for a session and hand that to the BFF.
//
// ⚠️ **The `cc_sess_` token never reaches this component.** The BFF puts it
// straight into an httpOnly cookie, exactly as `login/callback` does for the
// OAuth path. What passes through here is Supabase's own access token, for the
// one moment it takes to post it.
//
// ⚠️ **This form opens nothing on its own.** The Console runs the three D71.3
// checks again server-side, and refuses a person whose row does not permit the
// method. A reader who sees this form is not thereby an operator.

type Props = {
  url: string;
  anonKey: string;
  //: Where Supabase must send the person back. Empty means the LINK cannot
  //: return here, so the form says so rather than pretending.
  callback: string;
};

type Stage = "email" | "code";

export default function EmailCodeForm({ url, anonKey, callback }: Props) {
  const [stage, setStage] = useState<Stage>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const base = url.replace(/\/$/, "");

  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const res = await fetch(otpStartUrl(base, callback), {
        method: "POST",
        headers: { "content-type": "application/json", apikey: anonKey },
        // ⚠️ `otp.ts` holds both arguments — the wire field is `create_user`,
        // and `redirect_to` is what brings the emailed LINK back to us.
        body: JSON.stringify(otpStartBody(email)),
      });
      if (res.ok) {
        setStage("code");
        // ⚠️ **The LINK is the flow, not the code.** Supabase's default email
        // body carries a link and no digits, and this project cannot edit the
        // template — the dashboard locks template editing behind custom SMTP.
        // So the honest instruction is "click the link". The code box below
        // still works, and only for a project whose template renders
        // `{{ .Token }}`.
        setNote(
          callback
            ? "Check your email and click the sign-in link. It brings you straight back here."
            : "Email sent. ⚠️ This deployment has no callback address set, so the link cannot return you here — ask an admin to set OPERATOR_CONSOLE_ORIGIN.",
        );
        return;
      }
      setError(await refusal(res, "We could not send a code."));
    } catch {
      setError("The request did not complete — check the network, then retry.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${base}/auth/v1/verify`, {
        method: "POST",
        headers: { "content-type": "application/json", apikey: anonKey },
        body: JSON.stringify(otpVerifyBody(email, code)),
      });
      if (!res.ok) {
        setError(await refusal(res, "That code was not accepted."));
        return;
      }
      const body = (await res.json()) as { access_token?: string };
      if (!body.access_token) {
        setError("Supabase accepted the code and returned no token.");
        return;
      }

      // The same door the OAuth callback uses. The Console runs D71's checks
      // and answers 403 for a person with no row, which is a different problem
      // from a bad code and must read differently.
      const handoff = await fetch("/api/operator/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ access_token: body.access_token }),
      });
      if (handoff.ok) {
        window.location.href = "/";
        return;
      }
      if (handoff.status === 403) {
        setError(
          "That code was correct, and you are not on the operator registry. " +
            "Ask an admin to add you, and to allow the email method.",
        );
        return;
      }
      setError(await refusal(handoff, "Sign-in failed."));
    } catch {
      setError("The exchange did not complete — check the network, then retry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={stage === "email" ? sendCode : verify}>
      {stage === "email" ? (
        <>
          <label className="field-label" htmlFor="op-email">
            Work email
          </label>
          <input
            id="op-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(ev) => setEmail(ev.target.value)}
            placeholder="you@example.com"
          />
        </>
      ) : (
        <>
          <label className="field-label" htmlFor="op-code">
            Sent to {email}. Click the link in that email — or, if it shows a
            six-digit code, type it here.
          </label>
          <input
            id="op-code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            value={code}
            onChange={(ev) => setCode(ev.target.value)}
            placeholder="123456"
          />
        </>
      )}

      <button type="submit" disabled={busy}>
        {busy
          ? "Working…"
          : stage === "email"
            ? "Email me a code"
            : "Sign in"}
      </button>

      {stage === "code" && (
        <button
          type="button"
          className="linklike"
          disabled={busy}
          onClick={() => {
            setStage("email");
            setCode("");
            setError(null);
            setNote(null);
          }}
        >
          Use a different address
        </button>
      )}

      {note && <div className="field-hint">{note}</div>}
      {error && <div className="result err">{error}</div>}
    </form>
  );
}

//: Read Supabase's own message when it has one, and never invent a reason.
//: ⚠️ A rate-limit refusal is the one a reader will actually hit, because the
//: built-in mailer allows very few messages per hour. Saying "we could not
//: send a code" alone would send them to look for a typo they did not make.
async function refusal(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as {
    msg?: string;
    error_description?: string;
    message?: string;
    detail?: string;
  };
  const said =
    body.msg ?? body.error_description ?? body.message ?? body.detail;
  if (said) return said;
  if (res.status === 429) {
    return "Too many requests. The mail service allows only a few codes per hour.";
  }
  return `${fallback} (${res.status})`;
}
