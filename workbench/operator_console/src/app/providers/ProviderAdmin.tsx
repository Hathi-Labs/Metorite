"use client";

// Our provider accounts — WS-31 CP-10 slice 4, rebuilt as a vendor catalogue.
//
// 🔴 **What changed, and why it mattered.** The page drew a card only for a
// vendor we already held a key for. On a fresh install that is nothing, so the
// screen whose entire job is "install the first key" opened on an empty state
// and a row of small grey pills. You had to know that clicking a pill revealed
// a form somewhere further down the page. Every vendor is now a card, whether
// or not it has a key, and the setup guide opens ON the card you clicked.
//
// 🔴 **`Card` and `SetupPanel` are MODULE SCOPE, and that is not style.** A
// component declared inside `ProviderAdmin` is a new function object on every
// render, so React sees a different element type and remounts it. The panel
// holds the secret field: typing one character calls `setSecret`, re-renders,
// remounts the panel, and the caret is gone. The bug is invisible in a
// typecheck and in every test this app has, and it makes the one field this
// page exists for unusable. State comes down as `ctx` instead.
//
// ⚠️ **One card is open at a time.** `openFor` holds a slug, not a boolean per
// card. Several open panels would put several secret fields on one screen, and
// a pasted key is the one thing on this page that must not linger.
//
// ⚠️ **The secret lives in local state and nowhere else.** It is cleared the
// moment the Console answers, it is never put in a URL, never logged, and
// never read back — the Console's list query does not select the column it is
// stored in, so there is nothing to read back.
//
// ⚠️ **No GET runs from this component.** The page's server component reads
// the list with the caller's own token and passes it down. A client fetch
// would put the list on a path the browser can replay.

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import {
  KNOWN_PROVIDERS,
  type ProviderGuide,
  SECTIONS,
  guideFor,
  sectionOf,
  vendorLabel,
} from "@/lib/providerGuides";
import {
  type GroupStatus,
  type ProviderAccount,
  type ProviderGroup,
  byokOrgs,
  coverageLine,
  describeScope,
  groupByProvider,
  groupLine,
  groupStatus,
  healthLabel,
  healthTone,
  isLive,
  wouldRotate,
} from "@/lib/providers";
import { chipClass, type Tone } from "@/lib/tone";

type Props = { creds: ProviderAccount[] };

/** The word on a vendor card, and its tone.
 *
 * ⚠️ Keyed on `GroupStatus`, not `string`, so adding a fifth status is a
 * typecheck failure here rather than a card that renders a blank chip. */
const STATUS: Record<GroupStatus, { word: string; tone: Tone }> = {
  armed: { word: "armed", tone: "ok" },
  "byok-only": { word: "one org only", tone: "warn" },
  dropped: { word: "revoked", tone: "warn" },
  untouched: { word: "no key", tone: "neutral" },
};

type Filter = "all" | "installed" | "todo";

/** The key `openFor` holds while the free-text card is the open one.
 *
 * ⚠️ **The leading underscore is what makes it safe, and it is not decorative.**
 * `check_provider`'s regex requires a slug to START with `[a-z0-9]`, so no real
 * vendor can ever be spelled `_other` and collide with this. A sentinel that
 * merely looks unlikely — `other`, or a string with a leading space — is one
 * vendor sign-up away from being wrong. `providers.test.ts` pins the property
 * against the same regex the Console enforces. */
const OTHER = "_other";

/** Everything the install form needs, handed down rather than closed over. */
type Ctx = {
  provider: string;
  setProvider: (v: string) => void;
  secret: string;
  setSecret: (v: string) => void;
  showSecret: boolean;
  setShowSecret: (v: boolean) => void;
  label: string;
  setLabel: (v: string) => void;
  apiBase: string;
  setApiBase: (v: string) => void;
  orgSlug: string;
  setOrgSlug: (v: string) => void;
  guide: ProviderGuide | null;
  rotating: boolean;
  busy: boolean;
  submit: (e: React.FormEvent) => void;
  cancel: () => void;
};

/** One held credential. Live rows carry a health chip and a way out. */
function KeyRow({
  c,
  busy,
  onRevoke,
}: {
  c: ProviderAccount;
  busy: boolean;
  onRevoke: (c: ProviderAccount) => void;
}) {
  return (
    <div className={`keyrow ${isLive(c) ? "" : "dead"}`}>
      <div className="keymain">
        <span className="keylabel">{c.label ?? describeScope(c)}</span>
        <span className="muted small">
          {describeScope(c)}
          {c.apiBase ? ` · ${c.apiBase}` : ""}
          {c.createdAt ? ` · added ${c.createdAt.slice(0, 10)}` : ""}
        </span>
        {c.healthNote && <span className="muted small">{c.healthNote}</span>}
      </div>
      {isLive(c) ? (
        <>
          <span className={chipClass(healthTone(c.health))}>
            {healthLabel(c.health)}
          </span>
          <button
            type="button"
            className="linklike"
            onClick={() => onRevoke(c)}
            disabled={busy}
          >
            Revoke
          </button>
        </>
      ) : (
        <span className="chip">revoked</span>
      )}
    </div>
  );
}

/** The install form, rendered inside the card it belongs to — so the guide,
 *  the paste field and the vendor's name are one object on screen. */
function SetupPanel({ slug, ctx }: { slug: string; ctx: Ctx }) {
  const free = slug === OTHER;
  const { guide } = ctx;
  return (
    <form className="setup" onSubmit={ctx.submit}>
      {guide && (
        <>
          <div className="setup-links">
            <a className="getkey" href={guide.setupUrl} target="_blank" rel="noreferrer">
              Get an API key ↗
            </a>
            {guide.docsUrl && (
              <a
                className="linklike"
                href={guide.docsUrl}
                target="_blank"
                rel="noreferrer"
              >
                See the models ↗
              </a>
            )}
          </div>
          <ol className="setup-steps">
            {guide.steps.map((s, i) => (
              <li key={s}>
                <span className="stepno">{i + 1}</span>
                {s}
              </li>
            ))}
          </ol>
        </>
      )}

      {free && (
        <label>
          Vendor
          <input
            value={ctx.provider}
            onChange={(e) => ctx.setProvider(e.target.value)}
            placeholder="the litellm provider id, lowercase"
            required
          />
          <span className="field-hint">
            It must match the first part of the model ids you will declare. The
            Router looks the key up on exactly that word.
          </span>
        </label>
      )}

      <label>
        {free ? "Secret" : `Paste the ${vendorLabel(slug)} key`}
        <span className="secretbox">
          <input
            type={ctx.showSecret ? "text" : "password"}
            value={ctx.secret}
            onChange={(e) => ctx.setSecret(e.target.value)}
            placeholder={guide?.keyLooksLike ?? "the vendor API key"}
            autoComplete="off"
            spellCheck={false}
            required
          />
          <button
            type="button"
            className="linklike peek"
            onClick={() => ctx.setShowSecret(!ctx.showSecret)}
          >
            {ctx.showSecret ? "hide" : "show"}
          </button>
        </span>
        {guide?.keyLooksLike && (
          <span className="field-hint">
            It looks like <span className="mono">{guide.keyLooksLike}</span>.
            Paste it whole — a key with a space at either end is refused rather
            than stored broken.
          </span>
        )}
      </label>

      <details className="advanced">
        <summary>Where it goes, and who it is for</summary>
        <label>
          Label <span className="muted">(optional)</span>
          <input
            value={ctx.label}
            onChange={(e) => ctx.setLabel(e.target.value)}
            placeholder="what this account is, for the next person"
          />
        </label>
        <label>
          API base <span className="muted">(optional)</span>
          <input
            value={ctx.apiBase}
            onChange={(e) => ctx.setApiBase(e.target.value)}
            placeholder="leave empty for the vendor's own host"
          />
        </label>
        <label>
          One organization only <span className="muted">(optional)</span>
          <input
            value={ctx.orgSlug}
            onChange={(e) => ctx.setOrgSlug(e.target.value)}
            placeholder="leave empty to serve every customer"
          />
        </label>
      </details>

      {ctx.rotating && (
        <div className="banner">
          A live {ctx.provider.trim()} key already exists for{" "}
          {ctx.orgSlug.trim() || "every customer"}. Saving will REPLACE it. The
          old key is revoked and the new one installed together, so there is
          never a moment with two live keys or none.
        </div>
      )}

      <div className="job-actions">
        <button type="submit" disabled={ctx.busy}>
          {ctx.busy ? "Saving…" : ctx.rotating ? "Rotate the key" : "Install the key"}
        </button>
        <button type="button" className="linklike" onClick={ctx.cancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** One vendor: what it is, what we hold for it, and the way to install. */
function Card({
  g,
  open,
  ctx,
  onOpen,
  onRevoke,
}: {
  g: ProviderGroup;
  open: boolean;
  ctx: Ctx;
  onOpen: (slug: string) => void;
  onRevoke: (c: ProviderAccount) => void;
}) {
  const status = groupStatus(g);
  const chip = STATUS[status];
  const vguide = guideFor(g.provider);
  return (
    <section className={`provider-card ${open ? "open" : ""}`}>
      <header>
        <span className={categoricalChip(g.provider)}>
          <span className="glyph">{providerGlyph(g.provider)}</span>
          {vendorLabel(g.provider)}
        </span>
        <span className={chipClass(chip.tone)}>{chip.word}</span>
      </header>

      {/* ⚠️ The slug, always. It is what the operator must type as the first
          half of every model id they declare for this vendor. */}
      <span className="muted small mono">{g.provider}</span>
      {vguide && <p className="muted small">{vguide.description}</p>}
      <span className="muted small">{groupLine(g)}</span>

      {[...g.platform, ...g.byok, ...g.revoked].map((c) => (
        <KeyRow key={c.id} c={c} busy={ctx.busy} onRevoke={onRevoke} />
      ))}

      {open ? (
        <SetupPanel slug={g.provider} ctx={ctx} />
      ) : (
        <button
          type="button"
          className={status === "armed" ? "linklike add-job" : "setupbtn"}
          onClick={() => onOpen(g.provider)}
        >
          {status === "armed"
            ? "+ Add another key, or rotate this one"
            : `Set up ${vendorLabel(g.provider)}`}
        </button>
      )}
    </section>
  );
}

export default function ProviderAdmin({ creds }: Props) {
  const router = useRouter();

  const [openFor, setOpenFor] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [secret, setSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [apiBase, setApiBase] = useState("");
  const [label, setLabel] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  // 🔴 The known list is passed in, so a vendor with no key still gets a card.
  const groups = useMemo(() => groupByProvider(creds, KNOWN_PROVIDERS), [creds]);
  const live = creds.filter(isLive);
  const byok = byokOrgs(creds);
  const rotating = wouldRotate(creds, provider, orgSlug.trim() || null);

  const installed = groups.filter((g) => {
    const s = groupStatus(g);
    return s === "armed" || s === "byok-only";
  });

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups.filter((g) => {
      const s = groupStatus(g);
      const isInstalled = s === "armed" || s === "byok-only";
      if (filter === "installed" && !isInstalled) return false;
      if (filter === "todo" && isInstalled) return false;
      if (!q) return true;
      return (
        g.provider.includes(q) ||
        vendorLabel(g.provider).toLowerCase().includes(q) ||
        (guideFor(g.provider)?.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [groups, filter, query]);

  function startAdd(slug: string) {
    setOpenFor(slug);
    setProvider(slug === OTHER ? "" : slug);
    setSecret("");
    setShowSecret(false);
    setOrgSlug("");
    setLabel("");
    setApiBase("");
    setNote(null);
    setError(null);
  }

  function close() {
    // ⚠️ Clears the secret on the way out. A key left in a closed panel is
    // still in the page's memory and still in the DOM if the panel reopens.
    setOpenFor(null);
    setSecret("");
    setShowSecret(false);
  }

  async function install(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const r = await fetch("/api/operator/providers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          provider: provider.trim(),
          secret,
          api_base: apiBase.trim() || null,
          label: label.trim() || null,
          org_slug: orgSlug.trim() || null,
        }),
      });
      const text = await r.text();
      if (!r.ok) {
        // The Console is the authority on a refusal — it knows the provider
        // shape rule, the whitespace rule and the length rule. Paraphrasing
        // would be a second vocabulary for one 400.
        setError(`The Console refused: ${text}`);
        return;
      }
      // ⚠️ Cleared on success, always. A key left in a field survives a tab
      // switch and a screen share.
      setSecret("");
      setShowSecret(false);
      const body = JSON.parse(text) as { rotated?: boolean };
      const name = vendorLabel(provider.trim());
      setNote(
        body.rotated
          ? `Rotated. The previous ${name} key is revoked and no longer used.`
          : `${name} is installed. Declare a model for it next, on Models.`,
      );
      setOpenFor(null);
      router.refresh();
    } catch {
      setError("The request did not complete. Nothing was installed.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(c: ProviderAccount) {
    const what = c.orgSlug ? `${c.provider} for ${c.orgSlug}` : `${c.provider} (PLATFORM)`;
    const warn = c.orgSlug
      ? `Revoke ${what}? That organization falls back to our platform account.`
      : `Revoke ${what}?\n\nThis stops every AI call that is not BYOK.`;
    if (!confirm(warn)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/operator/providers/revoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: c.provider, org_slug: c.orgSlug }),
      });
      const text = await r.text();
      if (!r.ok) {
        setError(`The Console refused: ${text}`);
      } else {
        setNote(`Revoked ${what}.`);
        router.refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  const ctx: Ctx = {
    provider, setProvider,
    secret, setSecret,
    showSecret, setShowSecret,
    label, setLabel,
    apiBase, setApiBase,
    orgSlug, setOrgSlug,
    guide: guideFor(provider),
    rotating,
    busy,
    submit: install,
    cancel: close,
  };

  return (
    <>
      <div className={live.length === 0 ? "banner danger" : "note"}>
        {coverageLine(creds)}
        {byok.length > 0 && ` Organizations with their own key: ${byok.join(", ")}.`}
      </div>

      {error && <div className="banner danger">{error}</div>}
      {note && <div className="note">{note}</div>}

      <div className="toolbar">
        <div className="facetrow">
          {(
            [
              ["all", "All", groups.length],
              ["installed", "Installed", installed.length],
              ["todo", "Not set up", groups.length - installed.length],
            ] as [Filter, string, number][]
          ).map(([key, text, count]) => (
            <button
              key={key}
              type="button"
              className="facet"
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
            >
              {text}
              <span className="count">{count}</span>
            </button>
          ))}
        </div>
        <input
          className="search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a vendor…"
          aria-label="Find a vendor"
        />
      </div>

      {SECTIONS.map((section) => {
        const inSection = visible.filter((g) => sectionOf(g.provider) === section.key);
        if (inSection.length === 0) return null;
        return (
          <section key={section.key}>
            <div className="panel-head">
              <h2>{section.title}</h2>
              <p>{section.note}</p>
            </div>
            <div className="provider-grid">
              {inSection.map((g) => (
                <Card
                  key={g.provider}
                  g={g}
                  open={openFor === g.provider}
                  ctx={ctx}
                  onOpen={startAdd}
                  onRevoke={revoke}
                />
              ))}
            </div>
          </section>
        );
      })}

      {visible.length === 0 && (
        <div className="empty">
          <h2>No vendor matches that</h2>
          <p className="muted">Clear the search, or use the card below.</p>
        </div>
      )}

      {/* ── A vendor we have not written up ── */}
      <section className="panel">
        <div className="panel-head">
          <h2>Something else</h2>
          <p>
            Any vendor litellm can reach works here, written up or not. Use the
            provider id litellm knows it by, because the Router reads the vendor
            out of the model id and looks the key up on exactly that word.
          </p>
        </div>
        {openFor === OTHER ? (
          <SetupPanel slug={OTHER} ctx={ctx} />
        ) : (
          <button type="button" className="setupbtn" onClick={() => startAdd(OTHER)}>
            Install a key by hand
          </button>
        )}
      </section>
    </>
  );
}
