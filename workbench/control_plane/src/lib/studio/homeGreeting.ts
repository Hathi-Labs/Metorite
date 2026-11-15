// STU-LOOK-1: the first line of App Home. A pure function, so a test fixes the hour.

/** The first name of a member. An email or an empty name gives `null`, and the greeting has no name. */
export function firstName(name: string | null | undefined): string | null {
  const word = (name ?? "").trim().split(/\s+/)[0] ?? "";
  return word && !word.includes("@") ? word : null;
}

/** "Good morning, Mira." The hour is the local hour (0 to 23). */
export function homeGreeting(name: string | null | undefined, hour: number): string {
  const part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  const who = firstName(name);
  return who ? `Good ${part}, ${who}.` : `Good ${part}.`;
}
