/**
 * The pure rules of the My Day capture box (CC-1): which time a line names,
 * and what the toast says. No React and no network, so a test can reach both.
 *
 * A capture always files to the Inbox (D170). A line that names a clock time
 * for TODAY ("call the bank at 3 pm") also gets a reminder at that time. The
 * reminder is the write that Quick Capture already supports ("Remind me"),
 * so the box adds no second route. A time block needs a task with a
 * schedule, and the Inbox holds captures only, so a block waits for Clarify.
 */

/** A clock time that a line names, as a moment today. */
export interface NamedTime {
  /** The instant, in ms. */
  ms: number;
  /** The words of the time as the member typed them, for example "3 pm". */
  said: string;
}

const AMPM = /\b(?:at|@)\s*(\d{1,2})(?::([0-5]\d))?\s*([ap])\.?m\.?(?![a-z])/i;
const H24 = /\b(?:at|@)\s*([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*[ap]\.?m)/i;
const WORD = /\bat\s+(noon|midnight)\b/i;
/** A line about another day names no time of today. */
const OTHER_DAY =
  /\b(?:tomorrow|tmrw|yesterday|next\s+\w+|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?:st|nd|rd|th))\b/i;

/**
 * The clock time that `text` names for today, or null. Null also when the
 * time has passed today, when the line names another day, or when the hour
 * is not a real hour. `now` is the browser's clock, and the time is read on
 * the browser's wall clock, as Quick Capture reads it.
 */
export function namedTimeToday(text: string, now: Date): NamedTime | null {
  if (OTHER_DAY.test(text)) return null;
  let hour: number;
  let minute = 0;
  let said: string;
  let m = AMPM.exec(text);
  if (m) {
    const h12 = Number(m[1]);
    if (h12 < 1 || h12 > 12) return null;
    hour = (h12 % 12) + (m[3].toLowerCase() === "p" ? 12 : 0);
    minute = m[2] ? Number(m[2]) : 0;
    said = `${h12}${m[2] ? `:${m[2]}` : ""} ${m[3].toLowerCase()}m`;
  } else if ((m = H24.exec(text))) {
    hour = Number(m[1]);
    minute = Number(m[2]);
    said = `${String(hour).padStart(2, "0")}:${m[2]}`;
  } else if ((m = WORD.exec(text))) {
    const noon = m[1].toLowerCase() === "noon";
    hour = noon ? 12 : 0;
    said = noon ? "noon" : "midnight";
  } else {
    return null;
  }
  const at = new Date(now);
  at.setHours(hour, minute, 0, 0);
  // Midnight is the start of the day that has gone. It names no time today.
  if (said === "midnight" || at.getTime() <= now.getTime()) return null;
  return { ms: at.getTime(), said };
}

/** What the capture box did, for the toast. */
export interface CaptureOutcome {
  /** The reminder was made, at this time. */
  reminder?: NamedTime;
  /** The reminder was refused or failed, with the words of why. */
  reminderFailed?: string;
}

/** The title and the description of the toast. Undo undoes everything it names. */
export function captureToast(o: CaptureOutcome): { title: string; description?: string } {
  if (o.reminder) return { title: "Filed to your Inbox", description: `A reminder is set for ${o.reminder.said} today.` };
  if (o.reminderFailed) return { title: "Filed to your Inbox", description: `No reminder was set. ${o.reminderFailed}` };
  return { title: "Filed to your Inbox" };
}
