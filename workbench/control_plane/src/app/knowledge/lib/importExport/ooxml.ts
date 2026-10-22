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

/** Inflate the parts that `wanted` accepts, as text. Refuses over a limit. */
export async function readParts(data: Uint8Array, wanted: (name: string) => boolean): Promise<Map<string, string>> {
  await listParts(data);
  const { unzipSync, strFromU8 } = await import("fflate");
  let files: Record<string, Uint8Array>;
  try {
    files = unzipSync(data, { filter: (file) => wanted(file.name) });
  } catch {
    throw new DocumentRefusal("This file is damaged and cannot be read.");
  }
  const out = new Map<string, string>();
  let total = 0;
  for (const [name, content] of Object.entries(files)) {
    total += content.length;
    if (content.length > MAX_FILE_BYTES || total > MAX_FILE_BYTES * 4) {
      throw new DocumentRefusal(`A part of this file is over ${mb(MAX_FILE_BYTES)} when it is read.`);
    }
    out.set(name, strFromU8(content));
  }
  return out;
}

