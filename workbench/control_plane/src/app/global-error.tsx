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
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="bg-background text-foreground">
        <div className="flex min-h-screen items-center justify-center">
          <ErrorScreen error={error} reset={reset} />
        </div>
      </body>
    </html>
  );
}
