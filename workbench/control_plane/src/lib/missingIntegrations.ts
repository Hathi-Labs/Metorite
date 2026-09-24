import type { IntegrationStatus } from "@/app/api/integrations/status/route";

/**
 * The integrations an agent itself requires and that are not configured.
 *
 * The gateway puts `github` first in every agent's list as a platform
 * prerequisite (`routes/integrations.py`, "every agent needs repo cloning"),
 * and marks it mandatory. It is not something the agent asked for, and
 * AddAgentWizard already leaves it out of its mandatory list. The chat banner
 * did not, so every chat — the Projects assistant included, which declares no
 * integration at all — showed "1 integration not configured" on a box without
 * a GitHub token. `missingIntegrations.test.ts` is the fence.
 */
export function missingAgentIntegrations(
  statuses: readonly IntegrationStatus[],
): IntegrationStatus[] {
  return statuses.filter(
    (s) => s.service !== "github" && s.mandatory && !s.configured,
  );
}

/** "Gmail not configured", or "Gmail and Slack not configured". */
export function missingIntegrationsText(missing: readonly IntegrationStatus[]): string {
  const names = missing.map((s) => s.label || s.service);
  if (names.length === 0) return "";
  const list =
    names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `${list} not configured`;
}
