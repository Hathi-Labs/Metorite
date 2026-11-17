/**
 * CO-13 (`chat_organization.md` §17.5): the line under the chat header that
 * says which chat this one continues.
 *
 * - A source that the member can read: "Continued from <title>". The title
 *   opens that chat.
 * - A source that the member cannot read: "Continued from an earlier chat",
 *   plain text. No title and no link.
 * - A chat that continues nothing, or whose source is deleted: no line.
 *
 * The server decides which case it is (`continuedFrom` of the session list),
 * so this view reads no permission. It is a slim row of its own, so it
 * cannot overlap the buttons of the header at 390 px.
 */
import Button from "@/components/ui/Button";
import type { ChatSession } from "@/lib/sessions";

export const CONTINUED_UNREADABLE = "Continued from an earlier chat";

/** What the line says, or null for no line. `id` is null when it is plain text. */
export function continuedMarkLine(
  mark: ChatSession["continuedFrom"],
): { lead: string; title: string | null; fullTitle: string | null; id: string | null } | null {
  if (!mark) return null;
  if (!mark.id) return { lead: CONTINUED_UNREADABLE, title: null, fullTitle: null, id: null };
  const full = mark.title?.trim() || null;
  return { lead: "Continued from", title: full || "an earlier chat", fullTitle: full, id: mark.id };
}

export function ContinuedMark({
  mark,
  onOpen,
}: {
  mark: ChatSession["continuedFrom"];
  onOpen: (id: string) => void;
}) {
  const line = continuedMarkLine(mark);
  if (!line) return null;
  return (
    <p
      data-continued-mark=""
      className="flex min-w-0 shrink-0 items-center gap-1 px-3 py-1 text-xs text-muted-foreground"
    >
      <span className="shrink-0">{line.lead}</span>
      {line.id ? (
        <Button
          variant="text"
          size="none"
          layout="inline-flex min-w-0 items-center"
          title={line.fullTitle ? `Open ${line.fullTitle}` : "Open the earlier chat"}
          onClick={() => onOpen(line.id as string)}
          className="min-w-0 text-xs underline-offset-2 hover:underline"
        >
          <span className="min-w-0 truncate">{line.title}</span>
        </Button>
      ) : null}
    </p>
  );
}
