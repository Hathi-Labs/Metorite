/**
 * An agent's slug, read aloud: `projects-assistant` and `email_assistant`
 * both become words, "Projects assistant" and "Email assistant".
 *
 * One rule, used in two places. The billing breakdown names the agent a
 * credit went on (`settings/billing/lib/spend.ts`), and the chat trail names
 * the agent that a step asked (`lib/toolSteps.ts`). The orchestrator gives
 * each agent a tool with `-` changed to `_` (`orchestrator/agents.py`), so
 * the trail sees `email_assistant` where billing sees `email-assistant`. Both
 * spellings read the same here.
 *
 * Only the first letter is raised, so a product name inside the slug keeps
 * the case that the agent gave it.
 *
 * Fences: `spend.test.ts` and `toolSteps.test.ts`.
 */
export function agentName(slug: string): string {
  const words = slug.replace(/[-_]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : slug;
}

/**
 * An agent's slug in its one spelling: `projects_assistant`, the tool the
 * orchestrator names for the agent, and `projects-assistant`, the agent,
 * are both `projects-assistant`. `paneForDelegate` (the app switch) and
 * `taskRefModeForTurn` (the task pills) read a hand-off through it, so the
 * two never disagree about which agent a turn asked.
 */
export function agentSlug(name: string | null | undefined): string {
  return String(name ?? "").trim().toLowerCase().replace(/_/g, "-");
}
