import { describe, expect, it } from "vitest";

import { XmlError, parseXml } from "./xml";

describe("parseXml", () => {
  it("refuses a DOCTYPE before it parses (no DOM is touched), with a billion-laughs body", () => {
    const bomb =
      '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><lolz>&lol2;</lolz>';
    expect(() => parseXml(bomb)).toThrow(XmlError);
    expect(() => parseXml("<!doctype x><x/>")).toThrow(/DOCTYPE/);
  });
});
