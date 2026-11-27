"use client";

import { useState } from "react";
import {
  connectProblem,
  connectWord,
  connectionState,
  resultLine,
  type ArohConnection,
} from "@/lib/aroh";
import { formatDate } from "@/lib/format";
import { chipClass } from "@/lib/tone";

// The client half of /aroh. It holds no credential: the hub token goes from
// the password field to the BFF once, and the Console stores it encrypted.
// The box key is minted and pushed by the Console and never comes back here.

type Result = { ok: boolean; text: string } | null;

async function post(path: string, body: unknown): Promise<Result> {
  // Must not throw: a dropped network is an answer, and the busy flag must
  // clear (the rule `customers/[slug]/Actions.tsx` states for its own helper).
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: res.ok, text: await res.text() };
  } catch {
    return { ok: false, text: "no answer — the network dropped before the Console replied." };
  }
}

function reload() {
  window.location.reload();
}

function Answer({ result }: { result: Result }) {
  if (!result) return null;
  if (result.ok) return <div className="result ok">✓ Done</div>;
  return (
    <div className="result err">
      The Console refused:
      {"\n"}
      {result.text}
    </div>
  );
}

export default function ArohConnect({ conn }: { conn: ArohConnection }) {
  const state = connectionState(conn);
  const last = resultLine(conn.last_result);
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>
            Router key <span className={chipClass(state.tone)}>{state.label}</span>
          </h2>
          <p>
            Aroh calls the Router with one key for every organization: the Aroh box
            key, with <code>{conn.capabilities.join(", ")}</code>. Connect mints it and
            hands it to Aroh. You never see or copy it.
          </p>
        </div>
        <ul className="muted small">
          <li>
            Hub: <code>{conn.base_url ?? conn.default_base_url ?? "—"}</code>
          </li>
          <li>
            Box:{" "}
            {conn.deployment ? (
              <code>{conn.deployment.label}</code>
            ) : (
              "none yet. Connect registers it."
            )}
          </li>
          <li>
            Key: {conn.key_prefix ? <code>{conn.key_prefix}…</code> : "none"}
            {conn.key_capabilities ? ` (${conn.key_capabilities.join(", ")})` : ""}
            {conn.connected_at ? `, since ${formatDate(conn.connected_at)}` : ""}
          </li>
          {conn.hub && <li>Hub now: {resultLine(conn.hub) ?? "—"}</li>}
          {last && <li>Last act: {last}</li>}
        </ul>
      </section>
      <div className="row">
        <SettingsForm conn={conn} />
        <ConnectForm conn={conn} />
      </div>
    </>
  );
}

function SettingsForm({ conn }: { conn: ArohConnection }) {
  const [baseUrl, setBaseUrl] = useState(conn.base_url ?? conn.default_base_url ?? "");
  const [token, setToken] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const body: Record<string, unknown> = { app: "aroh", base_url: baseUrl.trim() };
    if (token.trim()) body.operator_token = token.trim();
    const r = await post("/api/operator/apps/connection", body);
    // Clear the field at once: the token must not stay in the page.
    setToken("");
    setResult(r);
    setBusy(false);
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={submit} autoComplete="off">
      <h2 style={{ marginTop: 0 }}>Aroh settings</h2>
      <p className="muted">
        The Console stores the operator token encrypted and never shows it again.
      </p>
      <label>Aroh base URL</label>
      <input
        value={baseUrl}
        placeholder={conn.default_base_url ?? "https://aroh.dev.metorite.com"}
        onChange={(e) => setBaseUrl(e.target.value)}
      />
      <label>Aroh operator token</label>
      <input
        type="password"
        value={token}
        autoComplete="new-password"
        placeholder={conn.token_set ? `stored, ends …${conn.token_last4 ?? ""}` : "paste once"}
        onChange={(e) => setToken(e.target.value)}
      />
      <div className="field-hint">
        {conn.token_set
          ? "Leave it empty to keep the stored token."
          : "Aroh shows this token on its admin page."}
      </div>
      <p className="muted small">Needs an admin.</p>
      <button
        type="submit"
        title={!baseUrl.trim() ? "Type the Aroh base URL." : "Save the URL, and the token when you typed one."}
        disabled={busy || !baseUrl.trim()}
      >
        {busy ? "Saving…" : "Save settings"}
      </button>
      <Answer result={result} />
    </form>
  );
}

function ConnectForm({ conn }: { conn: ArohConnection }) {
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);
  const connectWhy = connectProblem(conn, typed, "connect");
  const disconnectWhy = connectProblem(conn, typed, "disconnect");

  async function act(path: string) {
    setBusy(true);
    const r = await post(path, { app: "aroh", confirm: typed.trim() });
    setResult(r);
    setBusy(false);
    setTyped("");
    if (r?.ok) reload();
  }

  return (
    <form className="panel" onSubmit={(e) => e.preventDefault()}>
      <h2 style={{ marginTop: 0 }}>{connectWord(conn)}</h2>
      <p className="muted">
        🔴 Owner action. Connect mints a real key and hands it to Aroh. Rotate mints a
        new one, and the old one stops when Aroh has the new one. Disconnect revokes
        the key at once, then tells Aroh.
      </p>
      <label>Type aroh to confirm</label>
      <input value={typed} placeholder="aroh" onChange={(e) => setTyped(e.target.value)} />
      <p className="muted small">Needs an admin.</p>
      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          title={connectWhy ?? "Mint the Aroh box key and hand it to Aroh."}
          disabled={busy || connectWhy !== null}
          onClick={() => act("/api/operator/apps/connection/connect")}
        >
          {busy ? "Working…" : connectWord(conn)}
        </button>
        <button
          type="button"
          className="secondary"
          title={disconnectWhy ?? "Revoke the key, then tell Aroh to drop it."}
          disabled={busy || disconnectWhy !== null}
          onClick={() => act("/api/operator/apps/connection/disconnect")}
        >
          Disconnect
        </button>
      </div>
      <Answer result={result} />
    </form>
  );
}
