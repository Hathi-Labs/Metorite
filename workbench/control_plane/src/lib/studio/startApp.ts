import { shellChatOn } from "@/lib/shell/chatFlag";
import { openAssistant } from "@/lib/shell/assistant";
import { appBrief } from "@/lib/studio/packageLibrary";

/**
 * Start an app in chat: Mira builds it in the one assistant, from the brief.
 * The words wait in the box for the member to read and send. With the shell
 * chat off, `/chat?q=` does the same. It makes no app by itself, so an empty
 * seed opens a chat that asks the member what the app should track. The
 * library's "New app" button and the phone bar's "New app" tab both call it.
 */
export function startApp(seed: string, push: (url: string) => void): void {
  const brief = appBrief(seed);
  if (shellChatOn()) openAssistant({ draft: brief, fresh: true });
  else push(`/chat?q=${encodeURIComponent(brief)}`);
}
