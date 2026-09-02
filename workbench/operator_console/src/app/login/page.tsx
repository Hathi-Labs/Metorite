import {
  IDENTITY_FLAG,
  providerLabel,
  signinProvider,
  usesSessions,
} from "@/lib/identity";
import { directorySigninEnabled, emailCodeConfig } from "@/lib/otp";
import EmailCodeForm from "./EmailCodeForm";
import InterimForm from "./InterimForm";

export const dynamic = "force-dynamic";

// Sign-in — WS-31 CP-12g. Two paths, chosen by `OPERATOR_IDENTITY_ENABLED`.
//
// ⚠️ **A SERVER component on purpose.** The Supabase project URL is read here
// and only the finished authorize link reaches the browser. That avoids a
// `NEXT_PUBLIC_` variable, which would be a second place the same value lives
// and a second thing to keep in step.

//: Where Supabase sends the operator after the directory has answered. The
//: token comes back in the URL FRAGMENT, which a server never sees — so the
//: callback is a client page that reads it and posts it to the BFF.
//:
//: ⚠️ This exact URL must be on the project's redirect allowlist. Supabase
//: refuses anything else, which is correct and is why it is owner work (H-54).
const CALLBACK_PATH = "/login/callback";

// ⚠️ **The provider slug is NOT written here** (D70). `signinProvider()` is the
// one place that answers "which directory does this deployment use", and the
// Console reads the same variable server-side. Two opinions would mean a button
// that sends the operator to Google while the Console still demands an Entra
// `tid`, which refuses everybody.
function authorizeUrl(origin: string, provider: string): string | null {
  const base = (process.env.OPERATOR_SUPABASE_URL ?? "").trim();
  if (!base) return null;
  const redirect = `${origin}${CALLBACK_PATH}`;
  return (
    `${base.replace(/\/$/, "")}/auth/v1/authorize` +
    `?provider=${encodeURIComponent(provider)}` +
    `&redirect_to=${encodeURIComponent(redirect)}`
  );
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ origin?: string; error?: string }>;
}) {
  const params = await searchParams;

  if (!usesSessions()) {
    return <InterimForm />;
  }

  // `OPERATOR_CONSOLE_ORIGIN` rather than a guess from headers: a forwarded
  // host header is caller-controlled, and building a redirect target out of
  // one is how an open redirect happens.
  const origin = (process.env.OPERATOR_CONSOLE_ORIGIN ?? "").trim();
  const provider = signinProvider();
  const label = providerLabel();
  // ⚠️ **A button that cannot open is worse than no button.** Until the owner
  // configures the provider in Supabase, offering it sends the reader to an
  // error they will read as their own mistake. `OPERATOR_DIRECTORY_SIGNIN=0`
  // takes it off the page, and it defaults ON so no other box changes.
  const href =
    origin && directorySigninEnabled() ? authorizeUrl(origin, provider) : null;

  // The D71.3 fallback. `null` whenever the form could not work, so the page
  // shows nothing rather than a box that mails nobody. `otp.ts` says why, and
  // it REFUSES a key that is not publishable — that check is what keeps a
  // mispasted `service_role` key off a public login page.
  const emailCode = emailCodeConfig();

  // A real boolean, not the error string: JSX renders a truthy string, so
  // `params.error && …` in the guard would print the message a second time.
  // ⚠️ A reader who can still use the code form is NOT stranded, so the
  // recovery note stays off. It returns the moment neither door works.
  const stranded = (!href && !emailCode) || Boolean(params.error);

  return (
    <main className="login-center">
      <div className="panel login-card">
        <h1 style={{ marginBottom: 4 }}>
          Metorite <span className="muted">Operator Console</span>
        </h1>
        <p className="muted">
          Customer management for platform staff. Not for customers — they sign
          in at app.metorite.com.
        </p>

        {params.error && <div className="result err">{params.error}</div>}

        {href && (
          <a className="primary-cta" href={href}>
            Sign in with {label}
          </a>
        )}

        {/* ⚠️ **The code form sits BESIDE the directory button, never instead
            of it** (D71.4). The directory is the strong door, and the code is
            the fallback for a person who holds no account there. A page that
            offered only the code would quietly move every operator onto the
            weaker method, including the admin who adds operators. */}
        {emailCode && (
          <>
            {href && <div className="or-rule">or</div>}
            <EmailCodeForm url={emailCode.url} anonKey={emailCode.anonKey} />
          </>
        )}

        {!href && !emailCode && (
          <div className="banner">
            {/* Fails closed and says which value is missing, because the
                person reading this is the one who can set it. */}
            Sign-in is not configured on this deployment. Set{" "}
            <code>OPERATOR_SUPABASE_URL</code> and{" "}
            <code>OPERATOR_CONSOLE_ORIGIN</code> server-side, and add the
            callback URL to the Supabase redirect allowlist.
          </div>
        )}

        {/* The way back, printed on the page instead of left in a runbook.

            ⚠️ **This is TEXT, and it is deliberately NOT a second door.**
            `usesSessions()` picks ONE path, and the gate refuses the
            passphrase while the flag is on (§8 done-when 29). A console that
            accepted both at once would have two live doors while only one was
            being reasoned about. So this names the one env line that RETURNS
            the console to the interim path, and renders no form — a
            passphrase box here would 400 on submit.

            It shows in the two states that strand a reader: sign-in not
            configured, and a refused sign-in. */}
        {stranded && (
          <div className="field-hint">
            Cannot get in? Unset <code>{IDENTITY_FLAG}</code> server-side and
            restart the console. The staff passphrase works again after that
            restart. H-56 removes the passphrase for good, after one real
            sign-in has succeeded.
          </div>
        )}

        <div className="field-hint">
          You also need a row in the operator registry. Being in the directory
          is not enough, on purpose — ask an admin to add you.
        </div>
      </div>
    </main>
  );
}
