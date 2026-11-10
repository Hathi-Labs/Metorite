// The CRM proxy streams the whole-CRM ZIP (WS-53 CRM-X0 review P2).
//
// The gateway streams `GET /crm/export/all.zip`. A proxy that read the body
// whole would hold every record of the org in memory and meet its 30 s limit.
// These cases RUN the handler against a body that is still open.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "priya@fracktal.in" } }),
  isAuthEnabled: true,
}));

/** A body that sends one chunk and then stays open until `close()`. */
function openBody() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
      c.enqueue(new Uint8Array([0x50, 0x4b, 3, 4]));
    },
  });
  return { stream, close: () => controller.close() };
}

async function relay(path: string[], upstream: Response): Promise<Response> {
  vi.stubGlobal("fetch", vi.fn(async () => upstream));
  const { NextRequest } = await import("next/server");
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://localhost:3001/api/crm/${path.join("/")}`), {
    params: Promise.resolve({ path }),
  });
}

const within = <T,>(work: Promise<T>, ms: number) =>
  Promise.race([
    work,
    new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), ms)),
  ]);

describe("the CRM proxy and the whole-CRM ZIP", () => {
  // Load the route once before any clock starts, so a cold transform on a
  // busy machine is not read as a proxy that waits for the whole body.
  beforeAll(async () => {
    await import("next/server");
    await import("./route");
  }, 60_000);
  beforeEach(() => vi.unstubAllGlobals());

  it("answers before the gateway has finished the file", async () => {
    const body = openBody();
    const upstream = new Response(body.stream, {
      status: 200,
      headers: {
        "content-type": "application/zip",
        "content-disposition": 'attachment; filename="crm-export.zip"',
        "x-content-type-options": "nosniff",
      },
    });
    const res = await within(relay(["export", "all.zip"], upstream), 5000);
    expect(res, "the proxy waited for the whole body").not.toBe("timeout");
    const answer = res as Response;
    expect(answer.status).toBe(200);
    expect(answer.headers.get("content-type")).toBe("application/zip");
    expect(answer.headers.get("content-disposition")).toBe('attachment; filename="crm-export.zip"');
    expect(answer.headers.get("x-content-type-options")).toBe("nosniff");
    const reader = answer.body!.getReader();
    const first = await reader.read();
    expect(Array.from(first.value ?? [])).toEqual([0x50, 0x4b, 3, 4]);
    body.close();
    expect((await reader.read()).done).toBe(true);
  });

  it("still relays a refusal of the same route as JSON", async () => {
    const res = await relay(
      ["export", "all.zip"],
      new Response(JSON.stringify({ detail: "Forbidden" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      }),
    );
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ detail: "Forbidden" });
  });

  it("streams that one route only", async () => {
    const body = openBody();
    const res = await within(
      relay(["export", "leads.csv"], new Response(body.stream, { status: 200 })),
      500,
    );
    // A CSV export is still read whole, so the open body holds the answer.
    expect(res).toBe("timeout");
    body.close();
  });
});
