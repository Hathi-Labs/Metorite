"use client";

/**
 * BotIdentitySection — the notetaker's own Google account.
 *
 * Why this screen exists: Google **auto-declines anonymous participants**, so a
 * signed-out bot is refused before it can even knock, and the only fully
 * unattended way in is a signed-in account whose address is on the invite.
 * That made "is the bot signed in, and as whom?" the single most important fact
 * about the feature — and until now it was only answerable by SSH-ing to the
 * box and curling a loopback port.
 *
 * Two deliberate design choices:
 *
 * 1. **Status is the source of truth, not the sign-in response.** A scripted
 *    login drives a real browser through Google's pages and can outlive the
 *    proxy's 120 s ceiling, so a thrown error does not mean it failed. Every
 *    attempt — success or failure — is followed by a re-read of the status, and
 *    that is what we render.
 * 2. **The two failure walls are named, not merged.** Google either refuses a
 *    scripted login outright ("this browser or app may not be secure" — no
 *    password will ever work, the interactive path is the only way) or accepts
 *    the password and asks for something human (2FA). Those need opposite
 *    responses from the user, so they must never collapse into "sign-in failed".
 */

import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import { getBotIdentity, signInBot, signInBotInteractive } from "../lib/api";
import type { BotIdentity } from "../lib/types";

/** Pull the human-readable wall out of the gateway's error payload. */
function explainSignInError(raw: unknown): string {
  const text = String(raw instanceof Error ? raw.message : raw);
  if (/browser or app may not be secure|\bblocked\b/i.test(text)) {
    return (
      "Google refused a scripted sign-in for this account (it calls the " +
      "browser insecure). No password will get through — use “Sign in by hand” " +
      "below instead."
    );
  }
  if (/2fa|two-?step|verify it'?s you|needs_human/i.test(text)) {
    return (
      "The password was accepted, but Google wants a human step (2-step " +
      "verification or “verify it’s you”). Use “Sign in by hand” to finish it."
    );
  }
  if (/wrong password|incorrect|couldn'?t find your Google Account/i.test(text)) {
    return "Google rejected that email or password.";
  }
  if (/in a call|409/i.test(text)) {
    return "The notetaker is in a meeting right now — its browser is busy. Try again after.";
  }
  if (/timeout|aborted|502|network/i.test(text)) {
    // The dangerous case: this may well have SUCCEEDED. Never claim it failed.
    return (
      "The sign-in took longer than the request allows, so the result is " +
      "unknown — the status above is authoritative. If it still says signed " +
      "out, try again."
    );
  }
  return text;
}

export default function BotIdentitySection() {
  const [id, setId] = useState<BotIdentity | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<null | "scripted" | "interactive">(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setId(await getBotIdentity());
    } catch {
      setId({ supported: true, signed_in: null, unreachable: true });
    }
  }, []);

  useEffect(() => {
    let alive = true;
    getBotIdentity()
      .then((v) => alive && setId(v))
      .catch(() => alive && setId({ supported: true, signed_in: null, unreachable: true }))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  async function onScripted() {
    setBusy("scripted");
    setError(null);
    setNote(null);
    try {
      await signInBot(email.trim(), password);
      setPassword(""); // never keep it around after use
      setNote("Signed in.");
    } catch (e) {
      setError(explainSignInError(e));
    } finally {
      // The status — not the response — decides what we show.
      await refresh();
      setBusy(null);
    }
  }

  async function onInteractive() {
    setBusy("interactive");
    setError(null);
    setNote(null);
    try {
      const out = await signInBotInteractive();
      setNote(
        out.vnc_enabled
          ? `Google's sign-in page is open in the notetaker's browser for ${
              out.window_s ? Math.round(out.window_s / 60) : 10
            } minutes. Tunnel in with “ssh -L 6080:127.0.0.1:6080 <vps>” and open http://localhost:6080/vnc.html to finish it.`
          : "The browser is open, but VNC is switched off so there's no way to " +
            "see it. Set MEET_VNC=1 on the server and restart the notetaker " +
            "container first."
      );
    } catch (e) {
      setError(explainSignInError(e));
    } finally {
      await refresh();
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> Checking the notetaker&apos;s account…
      </div>
    );
  }
  // Recall and friends manage their own bot accounts — don't offer a no-op.
  if (!id || id.supported === false) return null;

  const signedIn = id.signed_in === true;

  return (
    <section>
      <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <Icon name="KeyRound" className="h-3.5 w-3.5" /> Notetaker account
      </h3>

      {/* Status first — it's the fact that decides whether joining can work. */}
      {id.unreachable ? (
        <p className="mb-3 flex items-start gap-1.5 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Couldn&apos;t reach the notetaker service, so its sign-in state is
          unknown. It may be restarting.
        </p>
      ) : signedIn ? (
        <p className="mb-3 flex items-start gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs">
          <Icon name="CheckCircle2" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
          <span>
            Signed in as <strong>{id.email || "a Google account"}</strong>. Put
            this address on a calendar invite and the notetaker joins on its own,
            with nobody clicking Admit.
          </span>
        </p>
      ) : (
        <p className="mb-3 flex items-start gap-1.5 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs">
          <Icon name="AlertTriangle" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
          <span>
            Not signed in, so Google will decline the notetaker — it refuses
            participants that aren&apos;t signed in, and being in the call
            yourself doesn&apos;t help. Sign it into a{" "}
            <strong>dedicated</strong> Google account, never your own.
          </span>
        </p>
      )}

      {!signedIn && !id.unreachable && (
        // The single most confusing part of setting this up: "Google account"
        // reads as "switch our email to Gmail". It doesn't — it's a login.
        <p className="mb-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <strong className="text-foreground">This is a login, not a mailbox.</strong>{" "}
          Google lets you sign up with an address you already own, which creates
          a sign-in identity and <em>no</em> Gmail inbox — mail for that address
          keeps flowing to wherever it goes today, and nothing about your email
          setup changes. It exists only so the notetaker&apos;s browser has
          something to sign in with. Use the same address you&apos;ll put on
          calendar invites: an invited, signed-in notetaker is admitted
          automatically, with nobody clicking Admit.
        </p>
      )}

      {id.profile === false && (
        <p className="mb-3 text-xs text-warning">
          No persistent browser profile is configured (MEET_PROFILE_DIR), so a
          sign-in wouldn&apos;t survive a restart. Fix that on the server first.
        </p>
      )}

      {!id.unreachable && (
        <>
          <div className="space-y-2">
            <input
              type="email"
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={id.email || "notetaker@yourdomain.com"}
              className="w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm"
            />
            <input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              className="w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm"
            />
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              onClick={onScripted}
              disabled={busy !== null || !email.trim() || !password}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
            >
              {busy === "scripted" && <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />}
              {signedIn ? "Sign in as someone else" : "Sign in"}
            </button>
            <button
              onClick={onInteractive}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-accent disabled:opacity-50"
              title="Opens Google's sign-in page in the notetaker's own browser so you can complete 2FA over VNC"
            >
              {busy === "interactive" ? (
                <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Icon name="Monitor" className="h-3.5 w-3.5" />
              )}
              Sign in by hand (2FA)
            </button>
          </div>

          <p className="mt-2 text-xs text-muted-foreground">
            Sign-in drives a real browser, so it can take a minute or two. The
            password is used once and never stored. 2-step verification is fine
            — use &ldquo;by hand&rdquo; for it.
          </p>

          {busy === "scripted" && (
            <p className="mt-2 text-xs text-muted-foreground">
              Working through Google&apos;s sign-in pages… don&apos;t close this.
            </p>
          )}
          {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
          {note && !error && <p className="mt-2 text-xs text-emerald-500">{note}</p>}
        </>
      )}
    </section>
  );
}
