import { tryVision, type Deps } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";
import { blockedResult, lengthBlocker, visionBlocker, visionForm } from "@/lib/voiceTry";

export const dynamic = "force-dynamic";

// POST → send one uploaded image and a question through the bound
// `tier-vision` chain (VOX-4, §31.5).
//
// ⚠️ `admin`, and NO elevation window, as "Try a decision". It calls the
// vendor with OUR platform key. The Console writes one audit row and no usage
// row. A refusal is relayed word for word.
//
// The size is checked BEFORE the Console is called, and after the gate: the
// header first, so a huge body is never read, then the file and the question.
async function send(request: Request, d: Deps) {
  const long = lengthBlocker("image", request.headers.get("content-length"));
  if (long) return blockedResult(long);
  let file: FormDataEntryValue | null = null;
  let question = "";
  try {
    const form = await request.formData();
    file = form.get("file");
    const q = form.get("question");
    question = typeof q === "string" ? q : "";
  } catch {
    file = null;
  }
  const blob = file instanceof Blob ? file : null;
  const blocked = visionBlocker(blob, question);
  if (blocked || !blob) return blockedResult(blocked ?? { status: 400, message: "Choose an image first." });
  return tryVision(visionForm(blob, question), d);
}

export async function POST(request: Request): Promise<Response> {
  return proxyToConsole((d) => send(request, d));
}
