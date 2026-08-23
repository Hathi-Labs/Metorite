"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  EMAIL_OTP_PROVIDER_ID,
  OTP_CODE_LENGTH,
  OTP_EMAIL_STORAGE_KEY,
  codeEntryState,
} from "@/lib/emailOtp";

import { signInErrorMessage } from "../errorCopy";

/**
 * Type the code Resend just emailed (CP-2d slice 2, clause 14).
 *
 * ## Why this is a plain GET form and not a `fetch`
 *
 * `@auth/core` verifies an email code at
 * `GET /api/auth/callback/<provider>?token=…&email=…` — byte-for-byte the URL
 * `lib/actions/signin/send-token.js` puts in a magic link. Submitting a native
 * GET form produces exactly that navigation, so the browser follows the session
 * cookie and the post-sign-in redirect the way it does for every other
 * provider. A `fetch` would receive the redirect as data and have to
 * re-implement what happens next.
 *
 * ⚠️ **Nothing here is a security boundary.** That callback URL is directly
 * reachable by anyone, with or without this page, which is exactly why the rate
 * limit lives server-side in `gateway/routes/email_otp.py` and never here
 * (clause 11, ruling H-F). This page's only job is to be somewhere to type.
 *
 * ## Where the address comes from
 *
 * Auth.js's verify-request redirect carries `provider` and `type` and **not**
 * the address, so `SignInForm` stashes what the person typed in `sessionStorage`
 * on its way out and this page reads it back. `?email=` in the query wins when
 * present. When neither is there — a different tab, a cleared store — the field
 * is shown and the person retypes it; that is a worse experience than a prefill
 * and a much better one than a dead end.
 *
 * ⚠️ The address is USER INPUT here and that is not an R11 breach: it is not
 * trusted as identity. Ownership is proven by the code round-trip, and the
 * SESSION email is Auth.js's own verified value (`auth.ts`'s jwt callback).
 *
 * ## Why the decision is a pure helper and not two lines of this component
 *
 * ⚠️ **The fallback arm was unusable and the cause was a decision taken over the
 * LIVE input value** (repair of review finding P1, round 2, 2026-08-23). This
 * component computed `known = canonicalOtpIdentifier(email) !== ""` — and `"a"`
 * canonicalises to `"a"`, so the first keystroke flipped `known` true and
 * unmounted the field being typed into. Nobody without a stash could ever finish
 * entering their address. `codeEntryState` in `lib/emailOtp.ts` now owns both
 * variables: `known` derives from the mount-time STASH alone, `submitEmail` is
 * the canonical form of whichever address applies. It lives in the lib layer
 * because vitest here is node-env — there is no DOM renderer in this package and
 * a decision written inside a component has no fence at all.
 */
function Form() {
  const searchParams = useSearchParams();
  const errorMessage = signInErrorMessage(searchParams.get("error"));
  const fromQuery = searchParams.get("email") ?? "";
  // The address the page was HANDED, and the only input to `known`. `?email=`
  // wins; the sessionStorage hand-off fills it in at mount. Written in exactly
  // one other place (the effect below) and never from typing.
  const [stash, setStash] = useState<string | null>(fromQuery || null);
  const [typed, setTyped] = useState("");
  const [code, setCode] = useState("");

  // Read in an effect: this component is prerendered on the server, where there
  // is no sessionStorage at all. Same reason `lib/panelMode.ts` reads its
  // preference in one.
  useEffect(() => {
    if (fromQuery) return;
    try {
      const stashed = window.sessionStorage.getItem(OTP_EMAIL_STORAGE_KEY);
      if (stashed) setStash(stashed);
    } catch {
      // Storage can be unavailable (private mode, a strict policy). The field
      // below is then simply shown, which is the same graceful path as a
      // person opening this page in a fresh tab.
    }
  }, [fromQuery]);

  // ⚠️ **Both variables come from the helper and neither is recomputed here.**
  // `known` answers "was an address handed to this page" (mount-time stash
  // only — deriving it from `typed` is the P1 defect); `submitEmail` is always
  // CANONICAL (finding P1a: `@auth/core`'s send leg minted the token against its
  // own normalised form and `callback/index.js:151-152` compares verbatim AFTER
  // `useVerificationToken` has spent the row, so a raw address burns the code and
  // then reports it wrong). The visible box only ever feeds `typed`.
  const { known, submitEmail } = codeEntryState(stash, typed);

  return (
    <div className="flex min-h-screen items-center justify-center p-10">
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-8 text-center">
        <h1 className="text-xl font-semibold">Check your email</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {known
            ? `We sent a ${OTP_CODE_LENGTH}-digit code to ${submitEmail}.`
            : `Enter your address and the ${OTP_CODE_LENGTH}-digit code we sent you.`}
        </p>

        {errorMessage && (
          <div className="mt-4 rounded-md border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {errorMessage}
          </div>
        )}

        <form
          method="get"
          action={`/api/auth/callback/${EMAIL_OTP_PROVIDER_ID}`}
          className="mt-6 flex flex-col gap-2"
        >
          {/* The ONE field named `email`, and it always carries the canonical
              form. Rendered unconditionally so there is no arrangement of this
              page that submits a raw address. */}
          <input type="hidden" name="email" value={submitEmail} />
          {!known && (
            <Input
              type="email"
              // Deliberately UNNAMED: a second `email` control would submit the
              // raw string beside the canonical one and `@auth/core` reads the
              // first, which is how this bug would come back.
              //
              // ⚠️ It feeds `typed`, never `stash` — writing the keystroke back
              // into the stash would make `known` true on the first character
              // and unmount this field mid-typing, which is exactly the P1
              // defect this arm was rewritten to remove.
              required
              autoComplete="email"
              inputSize="lg"
              placeholder="you@company.com"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          )}
          <Input
            // `name="token"` is the parameter `@auth/core`'s callback reads, and
            // the one a magic link carries. Renaming it breaks verification with
            // no visible symptom until somebody tries to sign in.
            name="token"
            required
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern={`[0-9]{${OTP_CODE_LENGTH}}`}
            maxLength={OTP_CODE_LENGTH}
            inputSize="lg"
            className="text-center tracking-[0.4em]"
            placeholder={"0".repeat(OTP_CODE_LENGTH)}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          />
          <Button type="submit" size="lg" className="w-full">
            Verify code
          </Button>
        </form>

        <p className="mt-4 text-xs text-muted-foreground">
          The code expires shortly.{" "}
          <a href="/signin" className="underline">
            Start again
          </a>{" "}
          to get a new one.
        </p>
      </div>
    </div>
  );
}

export default function CodeForm() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center p-10">
          <div className="text-muted-foreground text-sm">Loading...</div>
        </div>
      }
    >
      <Form />
    </Suspense>
  );
}
