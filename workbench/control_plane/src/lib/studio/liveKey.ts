/**
 * The cache keys of the reads of every live door: `doorPrefix` of a door that
 * `createRealDoor` made, whose scope is `<package id>:live`. A record write of
 * Mira drops them (`lib/assistantWrites.ts`), so a records card in the chat and
 * an open app page read the records again (STU-F5a).
 *
 * This file has NO import, on purpose. `lib/assistantWrites.ts` is on the
 * import path of the shell, and `lib/shell/shellImports.test.ts` refuses a
 * component under `lib/shell`. `realDoor.ts` pulls in the Button, so the key
 * rule lives here and `realDoor.ts` re-exports it.
 */
const LIVE_DOOR_KEY = /^studio:[0-9a-fA-F-]{36}:live:/;

export function isLiveDoorKey(key: string): boolean {
  return LIVE_DOOR_KEY.test(key);
}
