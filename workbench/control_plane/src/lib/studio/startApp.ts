import { shellChatOn } from "@/lib/shell/chatFlag";
import { appBrief } from "@/lib/studio/packageLibrary";
import { startBuild } from "@/lib/studio/startBuild";

/**
 * Start a new app, in ONE place (Studio G3a, with MOB-12's phone tab).
 *
 * "Build it" makes a draft package, opens its IDE, and opens the dock with the
 * brief waiting in the box (`startBuild`). With the shell chat off, `/chat?q=`
 * does what it did before. The library's "New app" button and the phone bar's
 * "New app" tab both call it, so the two never drift. It resolves to
 * `startBuild`'s result, or to null on the `/chat?q=` path.
 */
export async function startApp(
  seed: string,
  push: (url: string) => void,
): Promise<Awaited<ReturnType<typeof startBuild>> | null> {
  if (!shellChatOn()) {
    push(`/chat?q=${encodeURIComponent(appBrief(seed))}`);
    return null;
  }
  return startBuild(seed, { go: push });
}
