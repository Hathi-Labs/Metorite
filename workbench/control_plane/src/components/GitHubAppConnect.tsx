"use client";

/**
 * The GitHub card, and the return of the GitHub App connect flow
 * (WS-54 GH-3, `project-docs/specs/github_integration.md` §7).
 *
 * Two parts, one file, because they are one flow:
 *
 * - `GitHubAppConnect` is the body of the GitHub card. It shows the
 *   connection, and for `integrations:manage` it offers Connect GitHub and
 *   Disconnect. Connect asks the BFF for the install link and sends the
 *   browser to GitHub. When a personal access token is connected now, a
 *   dialog says first that the App replaces it (§7.4).
 *   A fold, "Advanced: use a personal access token", holds the token field
 *   (GH-7, §7.6). It is the path for a private agent repository, and for a
 *   GitHub account that will not install the App.
 * - `GitHubAppReturn` lives in the Connections view, where GitHub sends the
 *   browser back. `?github=confirm&account=<login>` opens the dialog
 *   "Connect <login> to <org>?". Only that click writes the connection. Every
 *   other code shows its one sentence. Then the address is cleared.
 *
 * `src/lib/githubConnect.ts` holds every decision and every word. This file
 * only draws them. Fence: `src/lib/githubConnect.test.ts`.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import { consoleAccess } from "@/app/integrations/lib/connections";
import {
  GITHUB_RETURN_TEXT,
  TOKEN_FOLD_HELP,
  TOKEN_FOLD_LABEL,
  confirmCopy,
  githubCardView,
  parseGitHubReturn,
  refusalText,
  replaceTokenCopy,
  safeInstallationUrl,
  tokenProblem,
  type GitHubAccount,
} from "@/lib/githubConnect";

async function readAccount(): Promise<GitHubAccount | null> {
  try {
    const res = await fetch("/api/integrations/github/account", { cache: "no-store" });
    return res.ok ? ((await res.json()) as GitHubAccount) : null;
  } catch {
    return null;
  }
}

async function startInstall(): Promise<string> {
  const res = await fetch("/api/integrations/github/app/start", { method: "POST" });
  const body = (await res.json().catch(() => null)) as { url?: string; detail?: string } | null;
  if (!res.ok || !body?.url) throw new Error(refusalText(body?.detail));
  return body.url;
}

function GitHubMark({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" className={`${className} shrink-0 text-foreground`}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export interface GitHubAppConnectProps {
  /** Called after a disconnect or a saved token, so the page reads its lists again. */
  onChange?: () => void;
  /** Called after a token is saved (the agent wizards go on with it). */
  onConfigured?: () => void;
  /** Open the Advanced fold at once (the agent wizards need a token). */
  advancedOpen?: boolean;
}

export default function GitHubAppConnect({ onChange, onConfigured, advancedOpen }: GitHubAppConnectProps) {
  const { access } = useAccess();
  const canManage = consoleAccess(access).canManage;
  const [info, setInfo] = useState<GitHubAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState(false);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [uninstallUrl, setUninstallUrl] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [savingToken, setSavingToken] = useState(false);
  const [tokenNote, setTokenNote] = useState<{ ok: boolean; text: string } | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setInfo(await readAccount());
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const view = githubCardView(info, canManage);
  const installation = safeInstallationUrl(info?.installation_url);

  async function goToGitHub() {
    setStarting(true);
    setError(null);
    try {
      // The BFF passes on an install link on https://github.com/apps/ only.
      window.location.assign(await startInstall());
    } catch (err) {
      setError(err instanceof Error ? err.message : refusalText(null));
      setStarting(false);
      setWarning(false);
    }
  }

  async function disconnect() {
    setDisconnecting(true);
    try {
      const res = await fetch("/api/integrations/github", { method: "DELETE" });
      const body = (await res.json().catch(() => null)) as { installation_url?: string | null } | null;
      if (!res.ok) throw new Error("Metorite could not disconnect GitHub. Try again.");
      setUninstallUrl(safeInstallationUrl(body?.installation_url));
      setConfirmingDisconnect(false);
      await refresh();
      onChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Metorite could not disconnect GitHub.");
      setConfirmingDisconnect(false);
    } finally {
      setDisconnecting(false);
    }
  }

  async function saveToken() {
    const problem = tokenProblem(token);
    if (problem) {
      setTokenNote({ ok: false, text: problem });
      return;
    }
    setSavingToken(true);
    setTokenNote(null);
    try {
      const res = await fetch("/api/integrations/github/token", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
      });
      const body = (await res.json().catch(() => null)) as { login?: string; detail?: string } | null;
      if (!res.ok) {
        setTokenNote({ ok: false, text: body?.detail ?? "Metorite could not save the token." });
        return;
      }
      setToken("");
      setTokenNote({ ok: true, text: `Connected as ${body?.login ?? "GitHub"}.` });
      await refresh();
      onChange?.();
      onConfigured?.();
    } catch {
      setTokenNote({ ok: false, text: "Metorite could not save the token." });
    } finally {
      setSavingToken(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
        <Icon name="Loader2" className="h-3 w-3 animate-spin" />
        Reading the GitHub connection…
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="github-card">
      <div className="flex items-start justify-between gap-4 rounded-lg border border-border bg-secondary/50 px-4 py-3">
        <div className="flex min-w-0 items-start gap-2">
          <GitHubMark className="mt-0.5 h-4 w-4" />
          <div className="min-w-0">
            <p className="text-sm text-foreground">{view.headline}</p>
            {view.mode === "app" && info?.connected_by ? (
              <p className="mt-0.5 text-xs text-muted-foreground">Connected by {info.connected_by}.</p>
            ) : null}
            {!info?.app_available && view.mode === "none" ? (
              <p className="mt-0.5 text-xs text-muted-foreground">
                The Metorite GitHub App is not turned on for this server yet.
              </p>
            ) : null}
          </div>
        </div>
        <span
          className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
            view.mode === "app" || view.mode === "token"
              ? "bg-success"
              : view.mode === "attention"
                ? "bg-warning"
                : "bg-muted"
          }`}
          aria-hidden="true"
        />
      </div>

      {canManage && (view.connectLabel || view.canDisconnect) ? (
        <div className="flex flex-wrap gap-2">
          {view.connectLabel && view.mode === "app" && installation ? (
            // A link that leaves for GitHub, in a new tab: a link, not a control.
            <a
              href={installation}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 self-center text-xs text-primary underline"
            >
              {view.connectLabel}
              <Icon name="ExternalLink" className="h-3 w-3" />
            </a>
          ) : view.connectLabel ? (
            <Button
              size="sm"
              icon="LogIn"
              loading={starting}
              onClick={() => (view.warnsOfToken ? setWarning(true) : void goToGitHub())}
            >
              {view.connectLabel}
            </Button>
          ) : null}
          {view.canDisconnect ? (
            <Button variant="destructive" size="sm" onClick={() => setConfirmingDisconnect(true)}>
              Disconnect
            </Button>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      {uninstallUrl ? (
        <p className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
          Metorite no longer reads this GitHub account. The Metorite App stays installed at GitHub.{" "}
          <a href={uninstallUrl} target="_blank" rel="noopener noreferrer" className="text-primary underline">
            Uninstall it at GitHub
          </a>
          .
        </p>
      ) : null}

      {canManage ? (
        <CollapsibleSection label={TOKEN_FOLD_LABEL} icon="KeyRound" defaultOpen={advancedOpen ?? false}>
          <div className="space-y-2 pt-1">
            <p className="text-xs text-muted-foreground">{TOKEN_FOLD_HELP}</p>
            <div className="flex gap-2">
              <Input
                type="password"
                autoComplete="off"
                spellCheck={false}
                aria-label="Personal access token"
                placeholder="ghp_…"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="flex-1 font-mono"
              />
              <Button size="sm" variant="secondary" loading={savingToken} onClick={() => void saveToken()}>
                Save token
              </Button>
            </div>
            {tokenNote ? (
              <p
                role={tokenNote.ok ? "status" : "alert"}
                className={`text-xs ${tokenNote.ok ? "text-success" : "text-destructive"}`}
              >
                {tokenNote.text}
              </p>
            ) : null}
          </div>
        </CollapsibleSection>
      ) : null}

      <ConfirmDialog
        open={warning}
        title={replaceTokenCopy().title}
        body={replaceTokenCopy().body}
        note={replaceTokenCopy().note}
        confirmLabel={replaceTokenCopy().confirmLabel}
        confirmVariant="primary"
        icon="LogIn"
        busy={starting}
        onConfirm={() => void goToGitHub()}
        onCancel={() => {
          if (!starting) setWarning(false);
        }}
      />
      <ConfirmDialog
        open={confirmingDisconnect}
        title="Disconnect GitHub?"
        body="Metorite stops reading pull requests and checks. The links on tasks stay."
        note={
          view.mode === "app"
            ? "The Metorite App stays installed at GitHub. You can uninstall it there after this."
            : null
        }
        confirmLabel="Disconnect"
        confirmVariant="destructive"
        busy={disconnecting}
        onConfirm={() => void disconnect()}
        onCancel={() => {
          if (!disconnecting) setConfirmingDisconnect(false);
        }}
      />
    </div>
  );
}

/**
 * The return of the connect flow, in the Connections view. It waits for the
 * account read, so the dialog names the org, then shows the outcome once and
 * clears the address.
 */
export function GitHubAppReturn({ onConnected }: { onConnected?: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const searchParams = useSearchParams();
  const handled = useRef(false);
  const [account, setAccount] = useState<string | null>(null);
  const [orgName, setOrgName] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const clear = useCallback(() => {
    router.replace("/integrations?view=connections", { scroll: false });
  }, [router]);

  useEffect(() => {
    if (handled.current) return;
    const outcome = parseGitHubReturn(searchParams);
    if (!outcome) return;
    handled.current = true;
    if (outcome.code === "confirm" && outcome.account) {
      const login = outcome.account;
      void readAccount().then((info) => {
        setOrgName(info?.organization_name ?? null);
        setAccount(login);
      });
      return;
    }
    const said = GITHUB_RETURN_TEXT[outcome.code];
    toast.show({
      key: "integrations-github-connect",
      variant: said.tone === "error" ? "error" : "success",
      title: said.text,
    });
    clear();
  }, [searchParams, toast, clear]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/integrations/github/app/confirm", { method: "POST" });
      const body = (await res.json().catch(() => null)) as { detail?: string } | null;
      if (!res.ok) {
        setError(refusalText(body?.detail));
        setBusy(false);
        return;
      }
      toast.show({
        key: "integrations-github-connect",
        variant: "success",
        title: GITHUB_RETURN_TEXT.connected.text,
      });
      setAccount(null);
      setBusy(false);
      onConnected?.();
      clear();
    } catch {
      setError(refusalText(null));
      setBusy(false);
    }
  }

  if (!account) return null;
  const copy = confirmCopy(account, orgName);
  return (
    <ConfirmDialog
      open
      title={copy.title}
      body={copy.body}
      note={copy.note}
      confirmLabel={copy.confirmLabel}
      confirmVariant="primary"
      icon="Link"
      busy={busy}
      onConfirm={() => void confirm()}
      onCancel={() => {
        if (busy) return;
        setAccount(null);
        clear();
      }}
    >
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </ConfirmDialog>
  );
}
