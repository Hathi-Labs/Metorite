// ── The parts of an Office file (WS-61 KB-AI-1) ──────────────────────────────
//
// A `.docx`, `.pptx` and `.xlsx` is a zip. A zip can lie about the size of a
// part (a zip bomb), so this reads in two passes, as `zip.ts` does. Pass one
// lists the parts and inflates none, and `checkParts` refuses over a limit.
// Pass two inflates the wanted parts only, and checks the real size again.

import { MAX_FILE_BYTES, MAX_OFFICE_PARTS, mb } from "./limits";

/** A refusal of one file. The import goes on with the other files. */
export class DocumentRefusal extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DocumentRefusal";
  }
}

export interface DeclaredPart {
  name: string;
  size: number;
}

/** Check the declared parts of an Office file. Pure. */
export function checkParts(parts: readonly DeclaredPart[]): void {
  if (parts.length > MAX_OFFICE_PARTS) {
    throw new DocumentRefusal(`This file has ${parts.length} parts. The limit is ${MAX_OFFICE_PARTS}.`);
  }
  let total = 0;
  for (const part of parts) {
    if (part.size > MAX_FILE_BYTES) {
      throw new DocumentRefusal(`A part of this file is ${mb(part.size)} when it is read. The limit is ${mb(MAX_FILE_BYTES)}.`);
    }
    total += part.size;
  }
  if (total > MAX_FILE_BYTES * 4) {
    throw new DocumentRefusal(`This file is ${mb(total)} when it is read. That is over the limit.`);
  }
}

/** List the parts of the zip without inflating any. */
export async function listParts(data: Uint8Array): Promise<DeclaredPart[]> {
  const { unzipSync } = await import("fflate");
  const parts: DeclaredPart[] = [];
  try {
    unzipSync(data, {
      filter: (file) => {
        parts.push({ name: file.name, size: file.originalSize });
        if (parts.length > MAX_OFFICE_PARTS) {
          throw new DocumentRefusal(`This file has more than ${MAX_OFFICE_PARTS} parts. The limit is ${MAX_OFFICE_PARTS}.`);
        }
        return false;
      },
    });
  } catch (err) {
    if (err instanceof DocumentRefusal) throw err;
    throw new DocumentRefusal("This file is not an Office file that can be read.");
  }
  checkParts(parts);
  return parts;
}

/** The slice of compressed input pushed at once. At most ~1032 times that is inflated before the next check. */
const PUSH_BYTES = 16 * 1024;

/**
 * Inflate the parts that `wanted` accepts, as bytes. Each part is inflated as a
 * stream and counted as it grows, so a part whose real size is over its
 * declared size (a zip bomb) stops at once and refuses the file. A part that is
 * shorter than declared refuses it too. Never trust the declared size alone.
 */
export async function readPartBytes(data: Uint8Array, wanted: (name: string) => boolean): Promise<Map<string, Uint8Array>> {
  const declared = new Map<string, number>();
  for (const p of await listParts(data)) if (!declared.has(p.name)) declared.set(p.name, p.size);
  const { Unzip, UnzipInflate, UnzipPassThrough } = await import("fflate");
  const damaged = () => new DocumentRefusal("This file looks damaged and cannot be read.");
  const out = new Map<string, Uint8Array>();
  let failure: DocumentRefusal | null = null;
  let total = 0;
  const unzip = new Unzip((file) => {
    if (failure || file.name.endsWith("/") || !wanted(file.name)) return;
    const size = declared.get(file.name);
    if (size === undefined || size > MAX_FILE_BYTES) {
      failure = damaged();
      return;
    }
    const chunks: Uint8Array[] = [];
    let got = 0;
    file.ondata = (err, chunk, final) => {
      if (failure) return;
      if (err) {
        failure = damaged();
        return;
      }
      got += chunk.length;
      total += chunk.length;
      if (got > size || total > MAX_FILE_BYTES * 4) {
        failure = damaged();
        file.terminate();
        return;
      }
      chunks.push(chunk);
      if (final) {
        if (got !== size) {
          failure = damaged();
          return;
        }
        const whole = new Uint8Array(got);
        let at = 0;
        for (const c of chunks) {
          whole.set(c, at);
          at += c.length;
        }
        out.set(file.name, whole);
      }
    };
    file.start();
  });
  unzip.register(UnzipInflate);
  unzip.register(UnzipPassThrough);
  try {
    for (let at = 0; at < data.length && !failure; at += PUSH_BYTES) {
      const end = Math.min(at + PUSH_BYTES, data.length);
      unzip.push(data.subarray(at, end), end >= data.length);
    }
  } catch {
    throw damaged();
  }
  if (failure) throw failure;
  for (const name of declared.keys()) if (wanted(name) && !name.endsWith("/") && !out.has(name)) throw damaged();
  return out;
}

/** Inflate the parts that `wanted` accepts, as text. Refuses over a limit. */
export async function readParts(data: Uint8Array, wanted: (name: string) => boolean): Promise<Map<string, string>> {
  const { strFromU8 } = await import("fflate");
  const out = new Map<string, string>();
  for (const [name, content] of await readPartBytes(data, wanted)) out.set(name, strFromU8(content));
  return out;
}
