import { tryTranscription, type Deps } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";
import { audioForm, blockedResult, fileBlocker, lengthBlocker } from "@/lib/voiceTry";

export const dynamic = "force-dynamic";

// POST → send one uploaded clip through the bound `tier-stt` chain (VOX-4,
// §31.5).
//
// ⚠️ `admin`, and NO elevation window, as "Try a decision". It calls the
// vendor with OUR platform key. The Console writes one audit row and no usage
// row. A refusal is relayed word for word.
//
// The size is checked BEFORE the Console is called, and after the gate: the
// header first, so a huge body is never read, then the file itself.
async function send(request: Request, d: Deps) {
  const long = lengthBlocker("audio", request.headers.get("content-length"));
  if (long) return blockedResult(long);
  let file: FormDataEntryValue | null = null;
  try {
    file = (await request.formData()).get("file");
  } catch {
    file = null;
  }
  const blob = file instanceof Blob ? file : null;
  const blocked = fileBlocker("audio", blob);
  if (blocked || !blob) return blockedResult(blocked ?? { status: 400, message: "Choose a .wav clip first." });
  return tryTranscription(audioForm(blob), d);
}

export async function POST(request: Request): Promise<Response> {
  return proxyToConsole((d) => send(request, d));
}
