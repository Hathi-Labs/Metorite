"use client";

// The last-resort screen — WS-31 console review, 2026-08-30.
//
// 🔴 **A down Console must not render Next's anonymous "Application error".**
// The reads in `read.ts` convert an UNCONFIGURED Console into the calm
// "missing" banner, but a Console that is configured and UNREACHABLE (the
// box is down, DNS broke, the port refused) throws a plain fetch TypeError —
// and before this file existed, that crashed every page to a generic screen
// with no retry and no words. This boundary is the floor under all of them.
//
// ⚠️ It deliberately does NOT print `error.message`. A server-side fetch
// error can carry hostnames and ports; the operator needs "the Console did
// not answer", and the terminal running the box has the detail.

export default function ConsoleError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="login-center">
      <div className="panel login-card">
        <h1 style={{ marginBottom: 4 }}>The Console did not answer</h1>
        <p className="muted">
          This page could not read the Console — usually the service is down
          or unreachable, not a fault in what you did. Nothing you submitted
          was lost silently: a write either landed and shows after a retry,
          or was refused with it.
        </p>
        <button type="button" onClick={() => reset()}>
          Try again
        </button>
      </div>
    </main>
  );
}
