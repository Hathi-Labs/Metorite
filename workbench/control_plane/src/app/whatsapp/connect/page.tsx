"use client";

// WhatsApp Connect wizard (W11) — the guided, VERIFIABLE onboarding that turns
// Meta's fiddly Cloud API setup into four calm steps: what you need → point Meta
// at your inbox → paste + live-test your credentials → you're live. The "Test
// connection" step calls Meta's Graph API for real, so you never save a broken
// token. Honest by design: it names exactly what Meta requires and never fakes a
// one-click flow the platform can't actually deliver without app review.

import Icon from "@/components/Icon";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  createAccount,
  embeddedSignup,
  fetchBridgeStatus,
  fetchConnectionInfo,
  startBridgeSession,
  verifyConnection,
} from "../lib/api";
import type { WaConnectionInfo, WaVerifyResult } from "../lib/types";

const STEPS = ["Prerequisites", "Webhook", "Credentials", "Done"];

type ConnectMode =
  | "loading"
  | "pick" // choose transport: personal QR vs business cloud API
  | "personal" // whatsmeow QR pairing
  | "choose" // business: embedded-signup chooser
  | "manual" // business: guided manual wizard
  | "done";

export default function ConnectPage() {
  const router = useRouter();
  const [info, setInfo] = useState<WaConnectionInfo | null>(null);
  const [mode, setMode] = useState<ConnectMode>("loading");
  const [step, setStep] = useState(0);

  useEffect(() => {
    fetchConnectionInfo().then((i) => {
      setInfo(i);
      sessionStorage.setItem("wa_verify_token", i.verify_token);
      // Land on the transport chooser: personal QR (simple, now) vs the Cloud
      // API business path.
      setMode("pick");
    });
  }, []);

  const goInbox = () => router.push("/whatsapp");

  // Subtitle reflects the chosen path.
  const subtitle =
    mode === "personal"
      ? "Personal number · scan a QR code"
      : mode === "choose" || mode === "manual"
        ? "WhatsApp Business · official Meta Cloud API"
        : "Pick how you want to connect";

  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col p-4 md:p-6">
      <div className="mb-6 flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/15 text-primary">
          <Icon name="MessageCircle" className="h-5 w-5" />
        </span>
        <div>
          <h1 className="text-[16px] font-semibold leading-tight">
            Connect WhatsApp
          </h1>
          <p className="text-[12px] text-muted-foreground">{subtitle}</p>
        </div>
      </div>

      {mode === "loading" && (
        <div className="mt-10 flex justify-center text-muted-foreground">
          <Icon name="Loader2" className="h-5 w-5 animate-spin" />
        </div>
      )}

      {mode === "pick" && (
        <PickTransport
          onPersonal={() => setMode("personal")}
          onBusiness={() => {
            setStep(0);
            setMode(info?.embedded_signup ? "choose" : "manual");
          }}
        />
      )}

      {mode === "personal" && (
        <PersonalPairing
          onBack={() => setMode("pick")}
          onDone={() => setMode("done")}
        />
      )}

      {mode === "choose" && info && (
        <ChooseConnect
          info={info}
          onManual={() => {
            setStep(0);
            setMode("manual");
          }}
          onDone={() => setMode("done")}
        />
      )}

      {mode === "manual" && (
        <>
          <Stepper step={step} />
          <div className="mt-6 flex-1">
            {step === 0 && (
              <StepPrereqs
                showOneClickHint={info?.embedded_signup === false}
                onNext={() => setStep(1)}
              />
            )}
            {step === 1 && (
              <StepWebhook
                info={info}
                onBack={() => setStep(0)}
                onNext={() => setStep(2)}
              />
            )}
            {step === 2 && (
              <StepCredentials
                onBack={() => setStep(1)}
                onConnected={() => setMode("done")}
              />
            )}
          </div>
        </>
      )}

      {mode === "done" && (
        <div className="mt-6">
          <StepDone onGo={goInbox} />
        </div>
      )}
    </div>
  );
}

// ── Transport chooser: personal QR vs business Cloud API (W15) ────────────────

function PickTransport({
  onPersonal,
  onBusiness,
}: {
  onPersonal: () => void;
  onBusiness: () => void;
}) {
  return (
    <div className="space-y-3">
      <button
        onClick={onPersonal}
        className="group flex w-full items-start gap-3 rounded-xl border border-border bg-background p-5 text-left transition hover:border-primary/60 hover:bg-primary/[0.03]"
      >
        <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/15 text-primary">
          <Icon name="Smartphone" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-semibold">
              Personal WhatsApp
            </span>
            <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-primary">
              Fastest
            </span>
          </div>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            Link your own number by scanning a QR code — the same way WhatsApp
            Web works. No Meta developer account, no tokens, live in a minute.
          </p>
        </div>
        <Icon name="ArrowRight" className="mt-1 h-4 w-4 shrink-0 text-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-primary" />
      </button>

      <button
        onClick={onBusiness}
        className="group flex w-full items-start gap-3 rounded-xl border border-border bg-background p-5 text-left transition hover:border-primary/60 hover:bg-primary/[0.03]"
      >
        <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <Icon name="Building2" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <span className="text-[14px] font-semibold">
            WhatsApp Business (Cloud API)
          </span>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            The official Meta route for a business number — templates, higher
            limits, and fully within WhatsApp&apos;s terms. Needs a Meta app and
            about 15 minutes of setup.
          </p>
        </div>
        <Icon name="ArrowRight" className="mt-1 h-4 w-4 shrink-0 text-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-primary" />
      </button>

      <p className="px-1 pt-1 text-[11px] leading-relaxed text-muted-foreground">
        Not sure? Use <b>Personal WhatsApp</b> for your own line right now — you
        can add a business number later; both live side by side.
      </p>
    </div>
  );
}

// ── Personal number: whatsmeow QR pairing (W15) ───────────────────────────────

function PersonalPairing({
  onBack,
  onDone,
}: {
  onBack: () => void;
  onDone: () => void;
}) {
  const [qr, setQr] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("starting");
  const [reachable, setReachable] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const accountId = useRef<string | null>(null);
  const doneRef = useRef(false);

  // Apply a start-session result. All setState lives here (a promise callback),
  // never synchronously inside an effect — the app's fetch-then-set pattern.
  const applySession = useCallback((res: Awaited<ReturnType<typeof startBridgeSession>>) => {
    if (!res.ok || !res.data) {
      setError(res.error ?? "Couldn't start pairing.");
      setStatus("error");
      return;
    }
    accountId.current = res.data.account_id;
    setQr(res.data.qr);
    setReachable(res.data.bridge_reachable);
    setStatus(res.data.status || "pairing");
  }, []);

  // Retry / "New code": reset the visible state, then start a fresh session.
  const restart = useCallback(() => {
    doneRef.current = false;
    setError(null);
    setStatus("starting");
    setQr(null);
    startBridgeSession().then(applySession);
  }, [applySession]);

  // Kick off a session on mount.
  useEffect(() => {
    startBridgeSession().then(applySession);
  }, [applySession]);

  // Poll status + refreshed QR until the phone scans it (status → live).
  useEffect(() => {
    const id = setInterval(async () => {
      if (!accountId.current || doneRef.current) return;
      const s = await fetchBridgeStatus(accountId.current);
      setReachable(s.bridge_reachable);
      if (s.qr) setQr(s.qr);
      setStatus(s.status);
      if (s.status === "live") {
        doneRef.current = true;
        clearInterval(id);
        onDone();
      }
    }, 2500);
    return () => clearInterval(id);
  }, [onDone]);

  const bridgeDown = status !== "starting" && !reachable;

  return (
    <Card>
      <div className="flex items-center gap-2">
        <Icon name="QrCode" className="h-4 w-4 text-primary" />
        <h2 className="text-[14px] font-semibold">Scan to link your WhatsApp</h2>
      </div>

      {bridgeDown ? (
        <BridgeUnreachable onRetry={restart} />
      ) : (
        <>
          <ol className="mt-2 space-y-0.5 text-[12.5px] text-muted-foreground">
            <li>1. Open WhatsApp on your phone.</li>
            <li>
              2. Tap <b>Settings → Linked devices → Link a device</b>.
            </li>
            <li>3. Point your phone at the code below.</li>
          </ol>

          <div className="mt-4 flex justify-center">
            <div className="flex h-[280px] w-[280px] items-center justify-center rounded-xl border border-border bg-white p-3">
              {qr ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={qr}
                  alt="WhatsApp pairing QR code"
                  width={256}
                  height={256}
                  className="h-full w-full"
                />
              ) : status === "error" ? (
                <div className="px-4 text-center text-[12px] text-red-500">
                  {error ?? "Couldn't load the QR code."}
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 text-muted-foreground">
                  <Icon name="Loader2" className="h-5 w-5 animate-spin" />
                  <span className="text-[11px]">Generating code…</span>
                </div>
              )}
            </div>
          </div>

          <div className="mt-3 flex items-center justify-center gap-1.5 text-[11.5px] text-muted-foreground">
            {status === "live" ? (
              <span className="font-semibold text-success">Linked!</span>
            ) : (
              <>
                <Icon name="Loader2" className="h-3 w-3 animate-spin" />
                Waiting for you to scan… the code refreshes automatically.
              </>
            )}
          </div>

          <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
            <b>Heads up:</b> linking a personal number this way is outside
            WhatsApp&apos;s official terms and carries a small risk to the
            account. It&apos;s great for your own line; use the Cloud API for a
            business number.
          </div>
        </>
      )}

      {error && !bridgeDown && status !== "error" && (
        <div className="mt-3 rounded-md bg-red-500/10 px-3 py-1.5 text-[11px] text-red-500">
          {error}
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <GhostButton onClick={onBack}>
          <Icon name="ArrowLeft" className="h-3.5 w-3.5" /> Back
        </GhostButton>
        <GhostButton onClick={restart}>
          <Icon name="RefreshCw" className="h-3.5 w-3.5" /> New code
        </GhostButton>
      </div>
    </Card>
  );
}

function BridgeUnreachable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mt-3">
      <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
        <Icon name="AlertTriangle" className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
        <div className="text-[12px] text-amber-700 dark:text-amber-400">
          <div className="font-semibold">The WhatsApp bridge isn&apos;t running</div>
          <p className="mt-1 leading-relaxed">
            Personal linking needs the local <code>whatsapp_bridge</code> service
            to be up and reachable from the gateway. Start it (see{" "}
            <code>apps/services/whatsapp_bridge/README.md</code>) and set{" "}
            <code>WHATSAPP_BRIDGE_URL</code> on the gateway, then retry.
          </p>
        </div>
      </div>
      <div className="mt-4 flex justify-end">
        <PrimaryButton onClick={onRetry}>
          <Icon name="RefreshCw" className="h-3.5 w-3.5" /> Retry
        </PrimaryButton>
      </div>
    </div>
  );
}

// ── Chooser + Embedded Signup (W12) ───────────────────────────────────────────

type FbLoginResponse = { authResponse?: { code?: string } | null };
type FbWindow = Window & {
  FB?: {
    init: (opts: Record<string, unknown>) => void;
    login: (cb: (r: FbLoginResponse) => void, opts: Record<string, unknown>) => void;
  };
  fbAsyncInit?: () => void;
};

function ChooseConnect({
  info,
  onManual,
  onDone,
}: {
  info: WaConnectionInfo;
  onManual: () => void;
  onDone: () => void;
}) {
  return (
    <Card>
      <h2 className="text-[14px] font-semibold">Connect in one click</h2>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        Log in with Facebook, pick your WhatsApp Business number, and you&apos;re
        done — no copy-pasting IDs or tokens. We finish the setup (token exchange
        and webhook subscription) for you.
      </p>
      <div className="mt-4">
        <EmbeddedSignupButton info={info} onDone={onDone} />
      </div>
      <div className="my-4 flex items-center gap-3 text-[10.5px] text-muted-foreground/70">
        <div className="h-px flex-1 bg-border" /> OR
        <div className="h-px flex-1 bg-border" />
      </div>
      <button
        onClick={onManual}
        className="w-full rounded-lg border border-border px-3 py-2 text-[12.5px] font-semibold text-muted-foreground hover:text-foreground"
      >
        Set up manually with my own credentials
      </button>
    </Card>
  );
}

function EmbeddedSignupButton({
  info,
  onDone,
}: {
  info: WaConnectionInfo;
  onDone: () => void;
}) {
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionInfo = useRef<{ phone_number_id?: string; waba_id?: string }>({});

  // Load + init the Facebook JS SDK once.
  useEffect(() => {
    const w = window as unknown as FbWindow;
    const init = () => {
      if (!w.FB) return;
      w.FB.init({
        appId: info.fb_app_id,
        autoLogAppEvents: true,
        xfbml: true,
        version: info.graph_version,
      });
      setReady(true);
    };
    if (w.FB) {
      init();
      return;
    }
    w.fbAsyncInit = init;
    if (!document.getElementById("wa-fb-sdk")) {
      const s = document.createElement("script");
      s.id = "wa-fb-sdk";
      s.src = "https://connect.facebook.net/en_US/sdk.js";
      s.async = true;
      s.defer = true;
      s.crossOrigin = "anonymous";
      document.body.appendChild(s);
    }
  }, [info.fb_app_id, info.graph_version]);

  // Capture the WABA + phone number the user picks in the popup.
  useEffect(() => {
    const onMessage = (ev: MessageEvent) => {
      try {
        if (!/facebook\.com$/.test(new URL(ev.origin).hostname)) return;
      } catch {
        return;
      }
      try {
        const data =
          typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
        if (data?.type === "WA_EMBEDDED_SIGNUP" && data?.data) {
          sessionInfo.current = {
            phone_number_id: data.data.phone_number_id,
            waba_id: data.data.waba_id,
          };
        }
      } catch {
        /* not our message */
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const launch = useCallback(() => {
    const w = window as unknown as FbWindow;
    if (!w.FB || busy) return;
    setError(null);
    w.FB.login(
      (resp: FbLoginResponse) => {
        const code = resp?.authResponse?.code;
        const si = sessionInfo.current;
        if (!code || !si.phone_number_id) {
          setError("Signup was cancelled, or no number was selected.");
          return;
        }
        setBusy(true);
        embeddedSignup({
          code,
          phone_number_id: si.phone_number_id,
          waba_id: si.waba_id ?? null,
        }).then((res) => {
          setBusy(false);
          if (res.ok) onDone();
          else setError(res.error ?? "Couldn't finish connecting.");
        });
      },
      {
        config_id: info.es_config_id,
        response_type: "code",
        override_default_response_type: true,
        extras: { setup: {}, featureType: "", sessionInfoVersion: "3" },
      }
    );
  }, [busy, info.es_config_id, onDone]);

  return (
    <div>
      <button
        onClick={launch}
        disabled={!ready || busy}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#1877F2] px-4 py-2.5 text-[13px] font-semibold text-white hover:opacity-95 disabled:opacity-60"
      >
        {busy ? (
          <Icon name="Loader2" className="h-4 w-4 animate-spin" />
        ) : (
          <Icon name="LogIn" className="h-4 w-4" />
        )}
        Continue with Facebook
      </button>
      {!ready && !error && (
        <p className="mt-2 text-center text-[10.5px] text-muted-foreground">
          Loading Facebook…
        </p>
      )}
      {error && (
        <div className="mt-2 rounded-md bg-red-500/10 px-3 py-1.5 text-[11px] text-red-500">
          {error}
        </div>
      )}
    </div>
  );
}

function Stepper({ step }: { step: number }) {
  return (
    <div className="flex items-center gap-2">
      {STEPS.map((label, i) => {
        const done = i < step;
        const active = i === step;
        return (
          <div key={label} className="flex flex-1 items-center gap-2">
            <div className="flex items-center gap-2">
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold ${
                  done
                    ? "bg-primary text-primary-foreground"
                    : active
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground"
                }`}
              >
                {done ? <Icon name="Check" className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <span
                className={`hidden text-[11px] sm:inline ${
                  active ? "font-semibold text-foreground" : "text-muted-foreground"
                }`}
              >
                {label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div
                className={`h-px flex-1 ${done ? "bg-primary/50" : "bg-border"}`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Step 1: prerequisites ─────────────────────────────────────────────────────

function StepPrereqs({
  onNext,
  showOneClickHint,
}: {
  onNext: () => void;
  showOneClickHint?: boolean;
}) {
  const items = [
    {
      title: "A Meta app with WhatsApp",
      body: "Create one (or open yours) and add the WhatsApp product.",
      href: "https://developers.facebook.com/apps",
      link: "developers.facebook.com/apps",
    },
    {
      title: "Your Phone number ID + WhatsApp Business Account ID",
      body: "Both are on the WhatsApp → API Setup page of your app.",
      href: "https://developers.facebook.com/docs/whatsapp/cloud-api/get-started",
      link: "Cloud API · Get started",
    },
    {
      title: "A permanent access token",
      body: "Create a System User in Business Settings and generate a token with the whatsapp_business_messaging and whatsapp_business_management permissions.",
      href: "https://developers.facebook.com/docs/whatsapp/business-management-api/get-started",
      link: "System user tokens",
    },
  ];
  return (
    <Card>
      <h2 className="text-[14px] font-semibold">Before you start</h2>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        You&apos;ll set up three things in Meta&apos;s dashboard, then paste them
        here. We&apos;ll test them against Meta before saving, so you never store a
        broken token.
      </p>
      {showOneClickHint && (
        <div className="mt-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
          Tip: set <code>WHATSAPP_APP_ID</code> and{" "}
          <code>WHATSAPP_ES_CONFIG_ID</code> on the server to unlock the one-click
          &ldquo;Continue with Facebook&rdquo; flow instead of this manual setup.
        </div>
      )}
      <ol className="mt-4 space-y-3">
        {items.map((it, i) => (
          <li key={it.title} className="flex gap-3">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-bold text-muted-foreground">
              {i + 1}
            </span>
            <div className="min-w-0">
              <div className="text-[12.5px] font-semibold">{it.title}</div>
              <div className="text-[11.5px] text-muted-foreground">{it.body}</div>
              <a
                href={it.href}
                target="_blank"
                rel="noreferrer"
                className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
              >
                {it.link} <Icon name="ExternalLink" className="h-3 w-3" />
              </a>
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-6 flex justify-end">
        <PrimaryButton onClick={onNext}>
          Continue <Icon name="ArrowRight" className="h-3.5 w-3.5" />
        </PrimaryButton>
      </div>
    </Card>
  );
}

// ── Step 2: webhook ───────────────────────────────────────────────────────────

function StepWebhook({
  info,
  onBack,
  onNext,
}: {
  info: WaConnectionInfo | null;
  onBack: () => void;
  onNext: () => void;
}) {
  const [domain, setDomain] = useState("");

  const webhookUrl =
    info?.base_configured && info.webhook_url
      ? info.webhook_url
      : domain
        ? `${domain.replace(/\/+$/, "")}/whatsapp/webhook`
        : "";

  return (
    <Card>
      <h2 className="text-[14px] font-semibold">Point Meta at your inbox</h2>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        In Meta → WhatsApp → Configuration, set the webhook below, click{" "}
        <b>Verify and save</b>, then subscribe to the <code>messages</code> field.
      </p>

      {!info ? (
        <div className="mt-4 flex items-center gap-2 text-[12px] text-muted-foreground">
          <Icon name="Loader2" className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {!info.base_configured && (
            <Field label="Your public gateway URL">
              <input
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="https://your-domain.com"
                className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-[12px] outline-none focus:border-primary"
              />
              <p className="mt-1 text-[10.5px] text-muted-foreground">
                The public HTTPS address of this Metorite gateway. (Set
                <code className="mx-1">WHATSAPP_PUBLIC_URL</code>on the server to
                skip this.)
              </p>
            </Field>
          )}
          <CopyRow label="Callback URL" value={webhookUrl} />
          <CopyRow label="Verify token" value={info.verify_token} />
        </div>
      )}

      <div className="mt-6 flex items-center justify-between">
        <GhostButton onClick={onBack}>
          <Icon name="ArrowLeft" className="h-3.5 w-3.5" /> Back
        </GhostButton>
        <PrimaryButton onClick={onNext} disabled={!webhookUrl}>
          I&apos;ve done this <Icon name="ArrowRight" className="h-3.5 w-3.5" />
        </PrimaryButton>
      </div>
    </Card>
  );
}

// ── Step 3: credentials + live test ───────────────────────────────────────────

function StepCredentials({
  onBack,
  onConnected,
}: {
  onBack: () => void;
  onConnected: () => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneId, setPhoneId] = useState("");
  const [wabaId, setWabaId] = useState("");
  const [token, setToken] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<WaVerifyResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canTest = phoneId.trim() && token.trim();
  const verified = result?.ok === true;

  const doTest = useCallback(async () => {
    if (!canTest || testing) return;
    setTesting(true);
    setResult(null);
    setError(null);
    const res = await verifyConnection({
      phone_number_id: phoneId.trim(),
      access_token: token.trim(),
    });
    setTesting(false);
    if (res.ok && res.data) {
      setResult(res.data);
      if (res.data.ok && res.data.display_phone_number && !phone)
        setPhone(res.data.display_phone_number);
    } else {
      setError(res.error ?? "Couldn't reach the server.");
    }
  }, [canTest, testing, phoneId, token, phone]);

  const doConnect = useCallback(async () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    const res = await createAccount({
      phone_number: (phone || result?.display_phone_number || "").trim(),
      phone_number_id: phoneId.trim(),
      waba_id: wabaId.trim() || null,
      display_name: displayName.trim() || result?.verified_name || "",
      webhook_verify_token: sessionStorage.getItem("wa_verify_token"),
      credentials: { access_token: token.trim() },
    });
    setSaving(false);
    if (res.ok) onConnected();
    else setError(res.error ?? "Couldn't connect the number.");
  }, [saving, phone, result, phoneId, wabaId, displayName, token, onConnected]);

  return (
    <Card>
      <h2 className="text-[14px] font-semibold">Enter your credentials</h2>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        Paste these from the WhatsApp → API Setup page. Nothing is saved until you
        test and connect; the token is encrypted at rest.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field label="Phone number ID *">
          <TextInput value={phoneId} onChange={setPhoneId} placeholder="1029384756…" />
        </Field>
        <Field label="Business account ID (WABA)">
          <TextInput value={wabaId} onChange={setWabaId} placeholder="optional" />
        </Field>
        <Field label="Display name">
          <TextInput
            value={displayName}
            onChange={setDisplayName}
            placeholder="e.g. Fracktal Works"
          />
        </Field>
        <Field label="Phone number">
          <TextInput value={phone} onChange={setPhone} placeholder="+91…" />
        </Field>
      </div>
      <div className="mt-3">
        <Field label="Permanent access token *">
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="EAAG…"
            className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-[12px] outline-none focus:border-primary"
          />
        </Field>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <GhostButton onClick={doTest} disabled={!canTest || testing}>
          {testing ? (
            <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Icon name="ShieldCheck" className="h-3.5 w-3.5" />
          )}
          Test connection
        </GhostButton>
        {verified && (
          <span className="text-[11px] font-semibold text-success">
            Verified with Meta
          </span>
        )}
      </div>

      {result?.ok && (
        <div className="mt-3 rounded-lg border border-success/30 bg-success/10 p-3">
          <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-success">
            <Icon name="CheckCircle2" className="h-4 w-4" />
            {result.verified_name || "Connected"}
          </div>
          <div className="mt-0.5 text-[11.5px] text-muted-foreground">
            {result.display_phone_number}
            {result.quality_rating && (
              <> · quality {result.quality_rating.toLowerCase()}</>
            )}
          </div>
        </div>
      )}
      {result && !result.ok && (
        <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-[11.5px] text-red-500">
          {result.error}
        </div>
      )}
      {error && (
        <div className="mt-3 rounded-md bg-red-500/10 px-3 py-1.5 text-[11px] text-red-500">
          {error}
        </div>
      )}

      <div className="mt-6 flex items-center justify-between">
        <GhostButton onClick={onBack}>
          <Icon name="ArrowLeft" className="h-3.5 w-3.5" /> Back
        </GhostButton>
        <PrimaryButton onClick={doConnect} disabled={!verified || saving}>
          {saving ? (
            <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Icon name="Check" className="h-3.5 w-3.5" />
          )}
          Connect number
        </PrimaryButton>
      </div>
      {!verified && (
        <p className="mt-2 text-right text-[10.5px] text-muted-foreground">
          Test the connection first, so a broken token is never saved.
        </p>
      )}
    </Card>
  );
}

// ── Step 4: done ──────────────────────────────────────────────────────────────

function StepDone({ onGo }: { onGo: () => void }) {
  return (
    <Card>
      <div className="flex flex-col items-center py-4 text-center">
        <span className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-success/15 text-success">
          <Icon name="CheckCircle2" className="h-7 w-7" />
        </span>
        <h2 className="text-[15px] font-semibold">You&apos;re connected 🎉</h2>
        <p className="mx-auto mt-1.5 max-w-sm text-[12.5px] text-muted-foreground">
          New messages will land in your triage queue as they arrive. Older chats
          aren&apos;t imported yet — coexistence history sync comes later — so your
          inbox starts fresh from now.
        </p>
        <PrimaryButton onClick={onGo} className="mt-5">
          Go to inbox <Icon name="ArrowRight" className="h-3.5 w-3.5" />
        </PrimaryButton>
      </div>
    </Card>
  );
}

// ── shared bits ───────────────────────────────────────────────────────────────

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-background p-5">
      {children}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10.5px] font-bold uppercase tracking-wider text-muted-foreground/70">
        {label}
      </span>
      {children}
    </label>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-[12px] outline-none focus:border-primary"
    />
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copiedTimer.current) clearTimeout(copiedTimer.current);
  }, []);
  const copy = useCallback(async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
      copiedTimer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — the value is still selectable */
    }
  }, [value]);
  return (
    <div>
      <div className="mb-1 text-[10.5px] font-bold uppercase tracking-wider text-muted-foreground/70">
        {label}
      </div>
      <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 px-2.5 py-1.5">
        <code className="min-w-0 flex-1 truncate text-[11.5px]">
          {value || "—"}
        </code>
        <button
          onClick={copy}
          disabled={!value}
          className="flex shrink-0 items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-[10.5px] font-semibold text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          {copied ? (
            <>
              <Icon name="Check" className="h-3 w-3 text-success" /> Copied
            </>
          ) : (
            <>
              <Icon name="Copy" className="h-3 w-3" /> Copy
            </>
          )}
        </button>
      </div>
    </div>
  );
}

function PrimaryButton({
  children,
  onClick,
  disabled,
  className = "",
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-[12.5px] font-semibold text-primary-foreground disabled:opacity-50 ${className}`}
    >
      {children}
    </button>
  );
}

function GhostButton({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-[12.5px] font-semibold text-muted-foreground hover:text-foreground disabled:opacity-50"
    >
      {children}
    </button>
  );
}
