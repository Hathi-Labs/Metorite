"use client";

/**
 * A page that throws, inside the shell. The sidebar stays, and the member
 * sees `ErrorScreen` in place of the page, never React's raw error text.
 * A tab that is out of date after a deploy reloads itself once.
 * Spec: `project-docs/specs/navigation_shell.md` §7.3.
 */

import ErrorScreen from "@/lib/shell/ErrorScreen";

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorScreen error={error} reset={reset} />;
}
