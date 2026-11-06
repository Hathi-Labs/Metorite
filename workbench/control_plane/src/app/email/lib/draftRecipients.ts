/**
 * The Cc that "Draft with AI" sends (task 1817). A forward goes to its To and
 * Cc, so the draft is a note to them. A reply and a new mail send no Cc, so
 * their request stays as it was.
 */
export function draftCc(mode: "reply" | "forward" | null | undefined, cc: string): string[] | undefined {
  if (mode !== "forward") return undefined;
  return cc.split(",").map((s) => s.trim()).filter(Boolean);
}
