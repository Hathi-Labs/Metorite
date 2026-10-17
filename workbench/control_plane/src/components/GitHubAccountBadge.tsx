"use client";

/**
 * GitHubAccountBadge
 *
 * Shows the currently connected GitHub account details and provides:
 *  - One-click "Import from GitHub CLI" when gh CLI is authenticated
 *  - A scope warning + command to copy when the `copilot` scope is missing
 *  - A "Reconnect" button that opens the device-flow wizard
 *
 * Used inside IntegrationCard for the "github" service, for both
 * the configured and unconfigured states.
 */

import React, { useCallback, useEffect, useState } from "react";
import type { GitHubAccountInfo } from "@/app/api/integrations/github/account/route";
import GitHubAppConnect from "@/components/GitHubAppConnect";
import GitHubDeviceConnect from "@/components/GitHubDeviceConnect";
import type { IntegrationStatus } from "@/app/api/integrations/status/route";

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

async function copyToClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
// ScopePill
// ---------------------------------------------------------------------------

function ScopePill({ scope, active }: { scope: string; active: boolean }) {
  return (
    <span
      className={`inline-flex text-[10px] px-1.5 py-0.5 rounded font-mono border ${
        active
          ? scope === "copilot"
            ? "bg-primary/10 text-primary border-primary/20"
            : "bg-secondary text-muted-foreground border-border"
          : "bg-card text-muted-foreground/70 border-border line-through"
      }`}
    >
      {scope}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface GitHubAccountBadgeProps {
  integration: IntegrationStatus;
  onRefresh: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GitHubAccountBadge({
  integration,
  onRefresh,
}: GitHubAccountBadgeProps) {
  const [info, setInfo] = useState<GitHubAccountInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{
    ok: boolean;
    login?: string;
    has_copilot?: boolean;
    refresh_command?: string | null;
    message?: string;
    error?: string;
  } | null>(null);
  const [showDeviceFlow, setShowDeviceFlow] = useState(false);
  const [copiedCmd, setCopiedCmd] = useState(false);

  const fetchInfo = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/integrations/github/account");
      if (res.ok) setInfo(await res.json());
    } catch {
      /* non-fatal */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchInfo();
  }, [fetchInfo]);

  const handleConnectCli = async () => {
    setImporting(true);
    setImportResult(null);
    try {
      const res = await fetch("/api/integrations/github/connect-cli", { method: "POST" });
      const data = await res.json();
      if (res.ok && data.ok) {
        setImportResult({ ok: true, ...data });
        await fetchInfo();
        onRefresh();
      } else {
        setImportResult({ ok: false, error: data.error ?? data.detail ?? "Unknown error" });
      }
    } catch (err) {
      setImportResult({ ok: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      setImporting(false);
    }
  };

  const handleCopyCmd = async (cmd: string) => {
    await copyToClipboard(cmd);
    setCopiedCmd(true);
    setTimeout(() => setCopiedCmd(false), 2000);
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
        <div className="w-3 h-3 border border-border border-t-muted-foreground rounded-full animate-spin" />
        Checking GitHub…
      </div>
    );
  }

  const isConfigured = integration.configured;

  return (
    <div className="space-y-3">
      {/* WS-54 GH-3: the Metorite GitHub App. */}
      <GitHubAppConnect onChange={onRefresh} />
      {/* ----------------------------------------------------------------- */}
      {/* Connected account card (shown when GITHUB_TOKEN is set)           */}
      {/* ----------------------------------------------------------------- */}
      {isConfigured && info?.token_login && (
        <div className="rounded-lg border border-border bg-secondary/50 px-4 py-3 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {/* GitHub mark */}
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4 text-foreground shrink-0">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
              </svg>
              <span className="text-sm font-medium text-foreground">@{info.token_login}</span>
              <span className="text-[10px] text-muted-foreground">connected</span>
            </div>
            {/* Scopes */}
            {info.token_scopes.length > 0 && (
              <div className="flex gap-1 flex-wrap mt-2 items-center">
                <span className="text-[10px] text-muted-foreground mr-1">scopes:</span>
                {["copilot", "repo"].map((s) => (
                  <ScopePill key={s} scope={s} active={info.token_scopes.includes(s)} />
                ))}
                {info.token_scopes
                  .filter((s) => s !== "copilot" && s !== "repo")
                  .map((s) => (
                    <ScopePill key={s} scope={s} active />
                  ))}
              </div>
            )}
            {/* Copilot scope warning */}
            {!info.token_has_copilot && (
              <div className="mt-2 flex items-start gap-2 text-[11px] text-warning bg-warning/10 border border-warning/20 rounded px-2 py-1.5">
                <span className="shrink-0 mt-0.5">⚠</span>
                <span>
                  Token lacks <code className="font-mono">copilot</code> scope — Copilot models
                  (GPT-4o, Claude Sonnet, o3-mini) are unavailable.
                </span>
              </div>
            )}
          </div>
          {/* Reconnect button */}
          <button
            onClick={() => setShowDeviceFlow((v) => !v)}
            className="shrink-0 text-[11px] px-2.5 py-1 rounded border border-border text-muted-foreground hover:bg-secondary transition-colors"
          >
            Reconnect
          </button>
        </div>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Refresh-scopes hint (copilot scope missing on configured token)   */}
      {/* ----------------------------------------------------------------- */}
      {isConfigured && info?.token_configured && !info.token_has_copilot && (
        <div className="rounded-lg border border-border bg-card/60 px-4 py-3 space-y-2">
          <p className="text-xs text-muted-foreground font-medium">Add Copilot scope</p>
          <p className="text-[11px] text-muted-foreground">
            Run this in your terminal, then click{" "}
            <span className="text-foreground">"Import from GitHub CLI"</span> below to update the stored token:
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-[11px] font-mono text-primary bg-secondary px-2 py-1 rounded">
              gh auth refresh --scopes copilot,repo
            </code>
            <button
              onClick={() => void handleCopyCmd("gh auth refresh --scopes copilot,repo")}
              className="shrink-0 text-[10px] px-2 py-1 rounded border border-border text-muted-foreground hover:bg-secondary transition-colors"
            >
              {copiedCmd ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* GitHub CLI import option                                           */}
      {/* ----------------------------------------------------------------- */}
      {info?.gh_cli_available && (
        <div
          className={`rounded-lg border px-4 py-3 ${
            info.gh_cli_authenticated
              ? "border-border bg-secondary/40"
              : "border-border bg-card/40"
          }`}
        >
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <p className="text-xs font-medium text-foreground">
                {info.gh_cli_authenticated
                  ? `Import from GitHub CLI ${info.gh_cli_login ? `(@${info.gh_cli_login})` : ""}`
                  : "GitHub CLI detected (not logged in)"}
              </p>
              {info.gh_cli_authenticated ? (
                <div className="flex gap-1 flex-wrap mt-1.5 items-center">
                  <span className="text-[10px] text-muted-foreground mr-1">CLI scopes:</span>
                  {["copilot", "repo"].map((s) => (
                    <ScopePill key={s} scope={s} active={info.gh_cli_scopes.includes(s)} />
                  ))}
                  {!info.gh_cli_has_copilot && (
                    <span className="text-[10px] text-warning ml-1">
                      (copilot scope missing)
                    </span>
                  )}
                </div>
              ) : (
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Run <code className="font-mono text-muted-foreground">gh auth login</code> first.
                </p>
              )}
            </div>
            {info.gh_cli_authenticated && (
              <button
                onClick={() => void handleConnectCli()}
                disabled={importing}
                className="shrink-0 text-xs px-3 py-1.5 rounded-lg bg-secondary hover:bg-muted disabled:opacity-40 text-foreground font-medium transition-colors"
              >
                {importing ? "Importing…" : isConfigured ? "Re-import" : "Import token"}
              </button>
            )}
          </div>

          {/* CLI import result */}
          {importResult && (
            <div
              className={`mt-3 text-[11px] rounded px-3 py-2 ${
                importResult.ok
                  ? "bg-success/10 text-success border border-success/20"
                  : "bg-destructive/10 text-destructive border border-red-500/20"
              }`}
            >
              {importResult.ok ? "✓ " : "✗ "}
              {importResult.ok ? importResult.message : importResult.error}
              {importResult.ok && importResult.refresh_command && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-warning">
                    ⚠ Add copilot scope:
                  </span>
                  <code className="font-mono text-primary bg-secondary px-1.5 py-0.5 rounded">
                    {importResult.refresh_command}
                  </code>
                  <button
                    onClick={() => void handleCopyCmd(importResult.refresh_command!)}
                    className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:bg-secondary"
                  >
                    {copiedCmd ? "Copied!" : "Copy"}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Device-flow fallback / "Reconnect" wizard                         */}
      {/* ----------------------------------------------------------------- */}
      {(!isConfigured || showDeviceFlow) && (
        <div className={isConfigured ? "border-t border-border pt-3 mt-1" : ""}>
          {isConfigured && (
            <p className="text-[10px] text-muted-foreground mb-2 uppercase tracking-wider">
              Or reconnect via OAuth device flow
            </p>
          )}
          <GitHubDeviceConnect
            integration={integration}
            onConfigured={() => {
              setShowDeviceFlow(false);
              void fetchInfo();
              onRefresh();
            }}
          />
        </div>
      )}
    </div>
  );
}
