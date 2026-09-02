"use client";

import { useEffect, useState } from "react";

// Where Supabase lands the operator after the directory has answered.
//
// ⚠️ **The copy here NAMES NO PROVIDER, and that is deliberate** (D70). This is
// a client component, so it cannot read `OPERATOR_SIGNIN_PROVIDER` — Next.js
// inlines a server variable only behind `NEXT_PUBLIC_`, and a second copy of
// the provider name is a second thing to keep in step. `login/page.tsx` already
// names the directory on the button the reader just pressed, so a message that
// says "the sign-in did not return a token" cannot contradict it. Hard-coding
// "Microsoft" here WOULD contradict it the day the owner flips the variable.
//
// ⚠️ **This has to be a CLIENT page, and the reason is structural.** Supabase
// returns the access token in the URL **fragment** (`#access_token=…`). A
// fragment is never sent to a server, so no server component can read it. This
// page reads it, hands it to the BFF, and replaces the URL immediately.
//
// The token is in `window.location.hash` for a moment and nowhere else. It is
// not written to state that outlives the exchange, and the `cc_sess_` token
// the BFF gets back never reaches this page at all — it goes straight into an
// httpOnly cookie.

function readHash(hash: string): { token?: string; error?: string } {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const error = params.get("error_description") ?? params.get("error");
  if (error) return { error };
  return { token: params.get("access_token") ?? undefined };
}

export default function CallbackPage() {
  const [message, setMessage] = useState("Signing you in…");

  useEffect(() => {
    const { token, error } = readHash(window.location.hash);

    // ⚠️ Clear the fragment BEFORE anything else can read it. It survives a
    // reload, sits in the address bar, and would be copied by anybody sharing
    // the URL. `replaceState` also keeps it out of the back button.
    window.history.replaceState(null, "", window.location.pathname);

    if (error) {
      setMessage("");
      window.location.href = `/login?error=${encodeURIComponent(error)}`;
      return;
    }
    if (!token) {
      window.location.href =
        "/login?error=" +
        encodeURIComponent("The sign-in did not return a token.");
      return;
    }

    void (async () => {
      try {
        const res = await fetch("/api/operator/session", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ access_token: token }),
        });
        if (res.ok) {
          window.location.href = "/";
          return;
        }
        // The Console's own refusal, relayed verbatim by the BFF. 403 means the
        // person is not on the registry, which is a different problem from a
        // token we could not verify, and the message must say which.
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          error?: string;
        };
        const detail =
          res.status === 403
            ? "You are not on the operator registry. Ask an admin to add you."
            : body.detail ?? body.error ?? `Sign-in failed (${res.status}).`;
        window.location.href = `/login?error=${encodeURIComponent(detail)}`;
      } catch {
        // A dropped exchange must not strand "Signing you in…" forever.
        window.location.href =
          "/login?error=" +
          encodeURIComponent(
            "The sign-in exchange did not complete — check the network and try again.",
          );
      }
    })();
  }, []);

  return (
    <main className="login-center">
      <div className="panel login-card">
        <p className="muted">{message}</p>
      </div>
    </main>
  );
}
