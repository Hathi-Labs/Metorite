# BETA-1

Tier: STANDARD

Gate: AGENT-SAFE. After A5c-1

## What

🔨 **BUILT, dark (2026-11-06).** **The library lists the packages of a member, and the app page deletes** (task 1809). A member could not find or delete an app that they built. **As built:** the Apps section of `/studio` lists the packages from `GET /api/studio/packages` beside the App Workshop apps, newest change first. A card shows the name, the state (Live or Draft) and the last change, and it opens `/studio/package-preview?pkg=<id>`. Each package counts in All and Mine, and a package with no live version counts in Drafts. The app page is titled by the package, and the "Package preview" title and the Preview badge are gone. An object with no declared `object:<name>.list` action shows one line: "Ask Mira to add a list action". The "Delete app" action reads `GET /studio/packages/{id}/delete-preview`, shows the counts, asks for a confirm, and sends `DELETE` with the token. It follows each 202 with its new token and shows the progress. A 200 returns to the library, a 409 shows the new counts, a 503 says to try again, and a 404 returns to the library. "Create an app", "New app" and "Describe the tool you need" open the one assistant with the brief in the box (`openAssistant` with `fresh`), or `/chat?q=` when the shell chat is off. They no longer make an App Workshop app. One BFF fix: `/api/studio/[[...path]]` dropped the body of a DELETE, so the gateway answered 422. It now forwards a JSON body and its content type (`route.test.ts`). `Retry-After` and 204 already passed. **Fix round 1:** a draft that asks for a new chat waits in the dock until the new chat is active (`draftFor` in `assistant.ts`), so the brief is not lost when a chat is open. After a busy or failed delete the page reads the preview again, so the next Confirm sends the token of the new counts. The loop stops when the page leaves, a deleted app leaves no cached page, the library holds its skeleton until both reads settle, and `/chat?q=` works with the shell bar off. The two delete requests are DEBT `P-9` in `chat_parity.py`, until the chat tool of A5c-2 lands. The debt row `POST /api/apps` is paid, so the ceiling of Custom Apps is 28.

## Done when

A member who publishes a package sees it in `/studio` and opens it. The app page deletes it. Fences: `src/lib/studio/packageLibrary.test.ts` and `src/lib/studio/packageDelete.test.ts` (R7). Visual review in light and dark, compact density, a changed accent, 1440 and 390 px
