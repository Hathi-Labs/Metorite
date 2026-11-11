// WS-17 SG-1 (email_app_master_plan.md §23.3.4, D-EM-67, D-EM-70): the
// signature editor uploads an image to a hosted https address and never
// embeds a `data:` URI. The runner has no DOM, so the wiring is a scan of the
// source, and the upload call and the URL rule run as plain functions.
import { readFileSync } from "node:fs";

import { getExtensionField } from "@tiptap/core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadSignatureImage } from "../lib/api";
import { shownError } from "../lib/signatureSave";
import { SIG_ALLOWED_URI, SIG_LOAD_URI, SigImage, UPLOAD_OFF } from "./SignatureEditor";

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
    expect(tab).toMatch(/const uploadOff = settings\.signature_image_upload === false;/);
    expect(tab).toMatch(/uploadAvailable=\{!uploadOff\}/);
  });

  it("says the sentence once: the save error drops it while the line shows", () => {
    const refused = `Your signature holds an embedded image, and this server cannot host it. ${UPLOAD_OFF}`;
    expect(shownError(refused, true)).toBe(
      "Your signature holds an embedded image, and this server cannot host it.");
    expect(shownError(UPLOAD_OFF, true)).toBeNull();
    expect(shownError(refused, false)).toBe(refused);
    expect(shownError("That file is not a picture.", true)).toBe("That file is not a picture.");
    expect(shownError(null, true)).toBeNull();
    const tab = read("./automation/ai-settings/SettingsTab.tsx");
    expect(tab).toMatch(/const errorText = shownError\(error, uploadOff\);/);
    expect(tab).toMatch(/\{errorText && \(\s*<p role="alert" data-signature-error/);
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

});

describe("signature editor: the load keeps what the server must judge (fix round 3)", () => {
  it.each([
    "http://acme.com/logo.png",
    "https://acme.com/logo.png",
    "mailto:jane@acme.example",
    "tel:+91987",
    "data:image/png;base64,iVBORw0KGgo=",
    "data:image/gif;base64,R0lGOD",
  ])("an editor load keeps %s, so a save sends it to the server", (url) => {
    expect(SIG_LOAD_URI.test(url)).toBe(true);
  });

  it.each(["javascript:alert(1)", "vbscript:x", "data:text/html;base64,PGI+", "//evil.example/x"])(
    "an editor load still drops %s", (url) => {
      expect(SIG_LOAD_URI.test(url)).toBe(false);
    },
  );

  it("an http: img survives the load: DOMPurify takes the load list", () => {
    expect(editor).toMatch(/ALLOWED_URI_REGEXP: SIG_LOAD_URI/);
    expect(editor).not.toMatch(/ALLOWED_URI_REGEXP: SIG_ALLOWED_URI/);
    // The image node of Tiptap parses any src (`img[src]`), see above.
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
