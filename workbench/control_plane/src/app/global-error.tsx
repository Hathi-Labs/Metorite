"use client";

/**
 * The root layout itself threw, so nothing of the shell is left to render.
 * Next requires this file to draw its own `<html>` and `<body>`. It still
 * shows `ErrorScreen`, never React's raw error text, and an out-of-date tab
 * still reloads itself once.
 * Spec: `project-docs/specs/navigation_shell.md` §7.3.
 */

import "./globals.css";

import ErrorScreen from "@/lib/shell/ErrorScreen";

export default function GlobalError({
  error,
  reset,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  reset: () => void;
  /** Next 16: refresh the server data, then reset. Absent on older Next. */
  unstable_retry?: () => void;
}) {
  return (
    <html lang="en">
      <body className="bg-background text-foreground">
        {/* Its own scroller: `globals.css` clips `html` and `body`, so the
            document never scrolls. On a short window this box scrolls to the
            bottom of the card. */}
        <div className="h-dvh overflow-auto">
          <div className="flex min-h-full items-center justify-center">
            <ErrorScreen error={error} reset={reset} retry={unstable_retry} />
          </div>
        </div>
      </body>
    </html>
  );
}
