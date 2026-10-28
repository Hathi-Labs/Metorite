// UXD-2 — a failed send never loses the mail. Tested on the REAL store with a
// mocked gateway.
//
// R7 fences named here:
//   * `email-send-failure-reopens`: a send that fails after the undo window
//     opens the composer again with the whole message and the reason.
//   * `email-send-failure-busy-composer`: when the member opened another
//     message in the wait, the failed one goes to Drafts, and the open
//     composer keeps its own text.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  sendEmail: vi.fn(),
  saveDraft: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, ...api };
});

import { useEmailStore } from "./emailStore";
import type { SendEmailParams } from "./api";

const message: SendEmailParams = {
  accountId: "box-1",
  to: ["a@x.test", "b@x.test"],
  cc: ["c@x.test"],
  subject: "Quote for 40 units",
  bodyText: "Hello,\n\nThe quote is attached.",
  attachments: [{ filename: "quote.pdf", mimeType: "application/pdf", contentB64: "AAAA" }],
};

beforeEach(() => {
  vi.useFakeTimers();
  api.sendEmail.mockReset();
  api.saveDraft.mockReset();
  useEmailStore.setState({
    composeOpen: true,
    composeDraftId: null,
    composeDefaults: null,
    pendingSend: null,
    error: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("email-send-failure-reopens", () => {
  it("opens the composer again with the whole message and the reason", async () => {
    api.sendEmail.mockRejectedValue(new Error("The server refused b@x.test"));
    await useEmailStore.getState().sendEmail(message);
    expect(useEmailStore.getState().composeOpen).toBe(false);

    await vi.runAllTimersAsync();

    const s = useEmailStore.getState();
    expect(api.sendEmail).toHaveBeenCalledTimes(1);
    expect(s.pendingSend).toBeNull();
    expect(s.composeOpen).toBe(true);
    expect(s.composeDefaults).toMatchObject({
      accountId: "box-1",
      to: "a@x.test, b@x.test",
      cc: "c@x.test",
      subject: "Quote for 40 units",
      attachments: message.attachments,
      sendError: "The server refused b@x.test",
    });
    expect(s.composeDefaults?.replyToBody).toContain("The quote is attached.");
    // The reason shows next to Send, not as a page banner.
    expect(s.error).toBeNull();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("a send that works opens nothing", async () => {
    api.sendEmail.mockResolvedValue({ id: "sent-1" });
    const fetchEmails = vi.fn();
    useEmailStore.setState({ fetchEmails });
    await useEmailStore.getState().sendEmail(message);
    await vi.runAllTimersAsync();
    const s = useEmailStore.getState();
    expect(s.composeOpen).toBe(false);
    expect(s.composeDefaults).toBeNull();
    expect(s.error).toBeNull();
    expect(fetchEmails).toHaveBeenCalled();
  });

  it("Undo still gives the message back with no reason", async () => {
    await useEmailStore.getState().sendEmail(message);
    useEmailStore.getState().undoSend();
    await vi.runAllTimersAsync();
    const s = useEmailStore.getState();
    expect(api.sendEmail).not.toHaveBeenCalled();
    expect(s.composeOpen).toBe(true);
    expect(s.composeDefaults?.to).toBe("a@x.test, b@x.test");
    expect(s.composeDefaults?.sendError).toBeUndefined();
  });
});

describe("email-send-failure-busy-composer", () => {
  it("keeps the failed message in Drafts and leaves the open composer alone", async () => {
    api.sendEmail.mockRejectedValue(new Error("Mailbox offline"));
    api.saveDraft.mockResolvedValue({ id: "draft-9", hasAttachments: true });
    await useEmailStore.getState().sendEmail(message);
    // The member starts a second message inside the undo window.
    const second = { to: "z@x.test", subject: "Other" };
    useEmailStore.setState({ composeOpen: true, composeDefaults: second });

    await vi.runAllTimersAsync();

    const s = useEmailStore.getState();
    expect(s.composeDefaults).toEqual(second);
    expect(api.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      accountId: "box-1",
      to: message.to,
      cc: message.cc,
      subject: message.subject,
      body: message.bodyText,
      attachments: message.attachments,
    }));
    expect(s.error).toBe("Mailbox offline. The message is in Drafts.");
  });

  it("says so when the draft cannot be saved either", async () => {
    api.sendEmail.mockRejectedValue(new Error("Mailbox offline"));
    api.saveDraft.mockRejectedValue(new Error("down"));
    await useEmailStore.getState().sendEmail(message);
    useEmailStore.setState({ composeOpen: true });
    await vi.runAllTimersAsync();
    expect(useEmailStore.getState().error).toBe("Mailbox offline. The message could not be saved.");
  });
});
