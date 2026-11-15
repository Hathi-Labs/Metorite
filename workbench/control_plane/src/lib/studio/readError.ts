/**
 * Which failed reads are worth a Retry (STU-LY-7, capture finding of task 3320). Pure.
 *
 * A read that failed for a reason that a second try cannot change, such as a refusal of a rule (422), a
 * record that is gone (404), a closed action (403) or a session that ended (401), must not offer Retry:
 * the button would fail the same way each time. A busy app (503), a lost network and the plain "did not
 * work" answer may pass the next time. The door gives a sentence and no code, so this reads the sentence.
 * A sentence that it does not know is not retried: a wrong "no Retry" is a smaller harm than a Retry that
 * never works.
 */
const RETRYABLE: readonly RegExp[] = [
  /^This app is busy now\./,
  /failed to fetch|network|offline|timed? ?out|connection/i,
  /^That did not work\. Try again\./,
];

/** True when a second try may work. */
export function retryableRead(message: string | null | undefined): boolean {
  if (!message) return false;
  return RETRYABLE.some((r) => r.test(message));
}
