/**
 * An answered blocking card stays answered (review round 1, P2-c).
 *
 * Mutations caught: the reader drops the Python repr that MAF can hand over;
 * it reads the result of another tool; `pickedFromAnswer` splits a label
 * that holds a comma; the stream drops the stamp of a card that came before
 * any text.
 */
import { describe, expect, it } from "vitest";

import { answersFromTools, pickedFromAnswer } from "./askAnswers";
import { withCustomEvent } from "./chatStream";

const RID = "0123456789abcdef0123456789abcdef";

describe("answersFromTools", () => {
  it("reads the request id and the response from a JSON result", () => {
    const got = answersFromTools([{ name: "emit_generative_ui",
      result: JSON.stringify({ ok: true, response: "Selected: Send a link", request_id: RID }) }]);
    expect(got.get(RID)).toBe("Selected: Send a link");
  });

  it("reads a Python dict repr too", () => {
    const got = answersFromTools([{ name: "emit_generative_ui",
      result: `{'ok': True, 'response': "Selected: Dana's pick", 'request_id': '${RID}'}` }]);
    expect(got.get(RID)).toBe("Selected: Dana's pick");
  });

  it("reads no other tool, and no card that got no answer", () => {
    expect(answersFromTools([
      { name: "send_email", result: JSON.stringify({ response: "x", request_id: RID }) },
      { name: "emit_generative_ui", result: JSON.stringify({ ok: true, response: null, request_id: RID }) },
      { name: "emit_generative_ui", result: "{'ok': True, 'response': None, 'request_id': '" + RID + "'}" },
    ]).size).toBe(0);
    expect(answersFromTools(undefined).size).toBe(0);
  });
});

describe("pickedFromAnswer", () => {
  const options = [
    { id: "fwd", label: "Forward with the PDF" },
    { id: "link", label: "Send a link" },
    { id: "comma", label: "Yes, send it" },
  ];

  it("maps the words the picker sends back to the option ids", () => {
    expect(pickedFromAnswer("Selected: Forward with the PDF, Send a link", options)).toEqual(["fwd", "link"]);
    expect(pickedFromAnswer("Selected: Yes, send it", options)).toEqual(["comma"]);
    expect(pickedFromAnswer("", options)).toEqual([]);
  });
});

describe("a card before the first text is stamped 0", () => {
  it("stamps the count of segments, 0 included", () => {
    const m = { id: "a", role: "assistant" as const, content: "", timestamp: 1 };
    expect(withCustomEvent(m, "generative_ui", {}).customEvents).toEqual([
      { name: "generative_ui", value: {}, segmentCutoff: 0 },
    ]);
    const two = { ...m, segments: [{ id: "s1", text: "a" }, { id: "s2", text: "b" }] };
    expect(withCustomEvent(two, "generative_ui", {}).customEvents?.[0].segmentCutoff).toBe(2);
  });
});
