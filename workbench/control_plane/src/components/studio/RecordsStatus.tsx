"use client";

/**
 * The load state of a record list in a Studio page toolbar (STU-D11b).
 *
 * `useCachedResource` gives `loading` and `error`. This line says them in plain words, and an
 * error brings a Retry. It draws nothing when the list is ready.
 */
import Button from "@/components/ui/Button";

export interface RecordsStatusProps {
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

export default function RecordsStatus({ loading, error, onRetry }: RecordsStatusProps) {
  if (error) {
    return (
      <span role="alert" className="inline-flex items-center gap-2 text-xs text-destructive">
        The records did not load. Try again.
        <Button variant="secondary" size="sm" onClick={onRetry}>Retry</Button>
      </span>
    );
  }
  if (loading) {
    return <span role="status" className="text-xs text-muted-foreground">Loading the records…</span>;
  }
  return null;
}
