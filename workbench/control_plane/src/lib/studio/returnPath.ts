/**
 * The ONE guard of a path back to Studio (WS-STU I8a, `ai_studio.md` §14 I8a).
 *
 * Studio's Connect in place sends the browser to the connect page of the owning app, with
 * `return=/studio/build?pkg=<id>`. The page that reads `return` sends the browser there only through
 * this guard. A leaf module with no Studio import, so the console reads it at no cost.
 */
import { safeLocalPath } from "@/lib/orgLink";

/** The query key of the return path. */
export const RETURN_PARAM = "return";
/** The one path prefix that a return may name. */
export const STUDIO_PREFIX = "/studio/";

const BASE = "https://studio-return.invalid";

/**
 * The return path when it is a path of this site under `/studio/`, else `null`.
 *
 * It reuses `safeLocalPath`, so `//host`, `/\host` and a control byte are refused. The path is then
 * read as the browser reads it, so `/studio/../admin` is `/admin`, and it is refused too.
 */
export function safeStudioReturn(raw: unknown): string | null {
  const path = safeLocalPath(raw);
  if (path === null) return null;
  let url: URL;
  try {
    url = new URL(path, BASE);
  } catch {
    return null;
  }
  if (url.origin !== BASE || !url.pathname.startsWith(STUDIO_PREFIX)) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}
