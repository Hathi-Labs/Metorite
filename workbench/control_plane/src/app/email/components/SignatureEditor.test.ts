// WS-17 SG-1 (email_app_master_plan.md §23.3.4, D-EM-67, D-EM-70): the
// signature editor uploads an image to a hosted https address and never
// embeds a `data:` URI. The runner has no DOM, so the wiring is a scan of the
// source, and the upload call and the URL rule run as plain functions.
import { readFileSync } from "node:fs";

import { getExtensionField } from "@tiptap/core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadSignatureImage } from "../lib/api";
import { SIG_ALLOWED_URI, SigImage, UPLOAD_OFF } from "./SignatureEditor";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const editor = read("./SignatureEditor.tsx");

describe("signature editor: no https address on the server (§23.3.9)", () => {
  it("turns the upload button off, with the plain sentence", () => {
    expect(editor).toMatch(/disabled=\{!uploadAvailable \|\| uploading \|\| !accountId\}/);
    expect(editor).toMatch(/title=\{!uploadAvailable \? UPLOAD_OFF :/);
    expect(editor).toMatch(/\{!uploadAvailable && \(\s*<p data-upload-off/);
  });

  it("says the server's words, and names no setting", () => {
    const server = read(
      "../../../../../../apps/services/gateway/gateway/routes/email/signature_images.py",
    );
    expect(server).toContain(`NOT_SET_UP = "${UPLOAD_OFF}"`);
    expect(UPLOAD_OFF).not.toMatch(/GATEWAY_PUBLIC_URL/);
  });

  it("takes the switch from the settings answer", () => {
    const tab = read("./automation/ai-settings/SettingsTab.tsx");
    expect(tab).toMatch(/uploadAvailable=\{settings\.signature_image_upload !== false\}/);
  });
});

describe("signature editor: no data: image", () => {
  it("no longer reads a file as a data URL", () => {
    expect(editor).not.toMatch(/readAsDataURL/);
    expect(editor).not.toMatch(/new FileReader/);
  });

  it("keeps a legacy data:image/png at load, so an edit cannot drop the logo", () => {
    // The parse rule that Tiptap gives the editor schema. With allowBase64
    // false it is `img[src]:not([src^="data:"])`, and a legacy image is gone
    // from the editor HTML after load. The save hosts the image instead.
    const parseHTML = getExtensionField(SigImage, "parseHTML", {
      name: SigImage.name, options: SigImage.options, storage: SigImage.storage,
    }) as () => { tag: string }[];
    const [rule] = parseHTML();
    expect(SigImage.options.allowBase64).toBe(true);
    expect(rule.tag).toBe("img[src]");
    expect(rule.tag).not.toContain("data:");
    // DOMPurify keeps a data: src on img by its default DATA_URI_TAGS. The
    // editor must not narrow that list.
    expect(editor).not.toMatch(/DATA_URI_TAGS:|ADD_DATA_URI_TAGS/);
  });

  it("the upload path never reads a file as a data URL", () => {
    const upload = editor.slice(editor.indexOf("const onPickImage"), editor.indexOf("if (!editor) {"));
    expect(upload).toMatch(/await upload\(accountId, file\)/);
    expect(upload).not.toMatch(/readAsDataURL|FileReader|data:/);
  });

  it("inserts the hosted URL that the upload answers", () => {
    expect(editor).toMatch(/await upload\(accountId, file\)/);
    expect(editor).toMatch(/setImage\(\{ src: image\.url/);
  });

  it("shows a refused upload in the server's words", () => {
    expect(editor).toMatch(/setUploadError\(\(e as Error\)\.message/);
    expect(editor).toMatch(/role="alert"/);
  });
});

describe("signature editor: the URL rule is the server's (D-EM-70)", () => {
  it.each(["https://acme.example/logo.png", "mailto:jane@acme.example", "tel:+91987"])(
    "keeps %s", (url) => {
      expect(SIG_ALLOWED_URI.test(url)).toBe(true);
    },
  );

  it.each([
    "http://track.example/p.png",
    "data:image/png;base64,iVBORw0KGgo=",
    "javascript:alert(1)",
    "//evil.example/x",
  ])("refuses %s", (url) => {
    expect(SIG_ALLOWED_URI.test(url)).toBe(false);
  });

  it("is the regex that the sanitiser uses", () => {
    expect(editor).toMatch(/ALLOWED_URI_REGEXP: SIG_ALLOWED_URI/);
  });
});

describe("uploadSignatureImage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the file as multipart to the email proxy, with the mailbox and the kind", async () => {
    const answer = {
      id: "i1", url: "https://api.example.test/email/signature-image/o/t.png",
      kind: "logo", content_type: "image/png", width: 480, height: 192,
      dark_share: 0, clear_share: 0,
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(answer), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([137, 80, 78, 71])], "logo.png", { type: "image/png" });

    const got = await uploadSignatureImage("acc-1", file);

    expect(got.url).toBe(answer.url);
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/email/signature/images");
    expect(init.method).toBe("POST");
    expect(init.headers).toBeUndefined(); // fetch sets the multipart boundary
    const form = init.body as FormData;
    expect(form.get("account_id")).toBe("acc-1");
    expect(form.get("kind")).toBe("logo");
    expect((form.get("file") as File).name).toBe("logo.png");
  });

  it("throws the server's sentence on a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "Image upload is not set up on this server." }), { status: 503 },
    )));
    const file = new File(["x"], "a.png", { type: "image/png" });
    await expect(uploadSignatureImage("acc-1", file)).rejects.toThrow(
      "Image upload is not set up on this server.",
    );
  });
});

describe("the email proxy passes a multipart body through", () => {
  const route = read("../../api/email/[...path]/route.ts");

  it("keeps the multipart type and the raw bytes", () => {
    expect(route).toMatch(/reqType\.startsWith\("multipart\/form-data"\)/);
    expect(route).toMatch(/Buffer\.from\(await req\.arrayBuffer\(\)\)/);
    expect(route).toMatch(/"Content-Type": isMultipart \? reqType : "application\/json"/);
  });
});
