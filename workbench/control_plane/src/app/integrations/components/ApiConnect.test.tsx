// API-5: the parts of the API connections flow, drawn as functions.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { connector } from "@/lib/connectors";

import { BOOKS_PROVIDER, parseBooksStatus, type ResultCard } from "../lib/apiConnect";
import {
  ApiConnectorRow,
  BooksPickBody,
  CONNECT_API_NOTE,
  ConnectApiBody,
  ConnectResultCardView,
  UsedBySection,
} from "./ApiConnect";

const noop = () => {};
const books = connector(BOOKS_PROVIDER)!;
const html = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);

const CARD: ResultCard = {
  provider: BOOKS_PROVIDER,
  label: "Zoho Books",
  works_now: { unlocks: books.unlocks, owning_app: "integrations", apps: [], mira: "Mira can now use Zoho Books when you ask." },
  templates: [],
  build_offer: true,
};

describe("the row of a T1 API", () => {
  it("shows Connect and no state chip before a connect", () => {
    const out = html(createElement(ApiConnectorRow, { connector: books, state: "not_connected", onConnect: noop }));
    expect(out).toContain("Zoho Books");
    expect(out).toContain(books.unlocks);
    expect(out).toContain('data-api-connect="not_connected"');
    expect(out).toContain(">Connect<");
    expect(out).not.toContain("Connected");
  });

  it("shows Connected only for the connected row, with Reconnect", () => {
    const out = html(createElement(ApiConnectorRow, { connector: books, state: "connected", onConnect: noop }));
    expect(out).toContain("Connected");
    expect(out).toContain("Reconnect");
  });

  it("shows the error of a refused start next to the row", () => {
    const out = html(
      createElement(ApiConnectorRow, { connector: books, state: "not_connected", error: "Zoho Books is not on.", onConnect: noop }),
    );
    expect(out).toContain('role="alert"');
    expect(out).toContain("Zoho Books is not on.");
  });
});

describe("Connect an API", () => {
  it("lists the T1 API first, then the old custom path", () => {
    const out = html(
      createElement(ConnectApiBody, {
        apis: [books],
        status: parseBooksStatus({ connected: true, status: "connected" }),
        busyId: null,
        error: null,
        onConnect: noop,
        onCustom: noop,
      }),
    );
    expect(out).toContain(CONNECT_API_NOTE);
    expect(out.indexOf("Zoho Books")).toBeLessThan(out.indexOf("Add another API"));
    expect(out).toContain("Connected");
  });

  it("draws a status of null (no flag, or no right) as not connected", () => {
    const out = html(
      createElement(ConnectApiBody, { apis: [books], status: null, busyId: null, error: null, onConnect: noop, onCustom: noop }),
    );
    expect(out).toContain('data-api-connect="not_connected"');
    expect(out).not.toContain("Connected");
  });
});

describe("the pick sheet", () => {
  const choices = [
    { id: "60001", name: "Acme India" },
    { id: "60002", name: "Acme Global" },
  ];

  it("lists each organization with one button, and saves nothing by itself", () => {
    const out = html(
      createElement(BooksPickBody, { choices, pickingId: null, error: null, over: false, onPick: noop, onStartAgain: noop }),
    );
    expect(out).toContain("2 Zoho Books organizations");
    expect(out).toContain("Acme India");
    expect(out).toContain("Organization ID 60002");
    expect(out.match(/Use this one/g)).toHaveLength(2);
    expect(out).not.toContain("Connect Zoho Books again");
  });

  it("offers a new connect when the pick is over, and shows why", () => {
    const out = html(
      createElement(BooksPickBody, {
        choices,
        pickingId: null,
        error: "This choice expired or belongs to another session. Connect Zoho Books again.",
        over: true,
        onPick: noop,
        onStartAgain: noop,
      }),
    );
    expect(out).toContain('role="alert"');
    expect(out).toContain("Connect Zoho Books again");
    expect(out.match(/disabled=""/g)?.length).toBeGreaterThanOrEqual(2);
  });
});

describe("the result card", () => {
  it("says what works now, that nothing uses it yet, and one next step as a link", () => {
    const out = html(createElement(ConnectResultCardView, { card: CARD, onNext: noop, onDismiss: noop }));
    expect(out).toContain('role="status"');
    expect(out).toContain("Zoho Books is connected.");
    expect(out).toContain("No app or chat tool uses it yet.");
    expect(out).toContain("Build an app on Zoho Books");
    expect(out).toContain('aria-label="Close the result"');
    expect(out).not.toContain('role="dialog"');
  });
});

describe("used by", () => {
  it("says plainly that nothing uses the connection", () => {
    const out = html(
      createElement(UsedBySection, { usedBy: { names: [], line: "No app, workflow or chat tool uses this connection yet." } }),
    );
    expect(out).toContain("Used by");
    expect(out).toContain("No app, workflow or chat tool uses this connection yet.");
  });

  it("lists each name when something uses it", () => {
    const out = html(createElement(UsedBySection, { usedBy: { names: ["Revenue board"], line: "" } }));
    expect(out).toContain("<li");
    expect(out).toContain("Revenue board");
  });
});
