"use client";

/**
 * GitHubAccountBadge — the body of the GitHub tile in Integrations.
 *
 * WS-54 GH-7 (`project-docs/specs/github_integration.md` §7.6). It draws the
 * one GitHub card, `GitHubAppConnect`: Connect GitHub through the Metorite
 * GitHub App, and the Advanced fold with the token field. The device flow, the
 * `gh` CLI import and the `connect-cli` call are retired, so this file calls
 * no route of its own. The props stay, so `ApiKeysView` needs no change.
 */

import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import GitHubAppConnect from "@/components/GitHubAppConnect";

interface GitHubAccountBadgeProps {
  integration: IntegrationStatus;
  onRefresh: () => void;
}

export default function GitHubAccountBadge({ onRefresh }: GitHubAccountBadgeProps) {
  return <GitHubAppConnect onChange={onRefresh} />;
}
