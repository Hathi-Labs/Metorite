import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CHAT_UPLOAD_MAX_BYTES } from "./chatUpload";
import { MAX_FILE_BYTES } from "@/app/knowledge/lib/importExport/limits";

// Task 3811: Next buffers only 10 MB of a request body for the proxy unless
// experimental.proxyClientMaxBodySize says more. A cut body reached the gateway
// and a 14 MB chat upload failed.
function configuredBytes(): number {
  const src = readFileSync(join(process.cwd(), "next.config.ts"), "utf-8");
  const m = src.match(/proxyClientMaxBodySize:\s*"(\d+)\s*(kb|mb|gb)"/i);
  if (!m) throw new Error("next.config.ts must set experimental.proxyClientMaxBodySize");
  const unit = { kb: 1024, mb: 1024 ** 2, gb: 1024 ** 3 }[m[2].toLowerCase() as "kb" | "mb" | "gb"];
  return Number(m[1]) * unit;
}

describe("proxy body limit", () => {
  it("covers the chat upload cap", () => {
    expect(configuredBytes()).toBeGreaterThanOrEqual(CHAT_UPLOAD_MAX_BYTES);
  });
  it("covers the Knowledge import file cap and the 50 MB workspace file", () => {
    expect(configuredBytes()).toBeGreaterThanOrEqual(MAX_FILE_BYTES);
    expect(configuredBytes()).toBeGreaterThanOrEqual(50 * 1024 * 1024);
  });
});
