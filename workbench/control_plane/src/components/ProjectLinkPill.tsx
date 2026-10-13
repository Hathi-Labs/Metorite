"use client";

/**
 * A link to a project in a task's text, drawn as the project pill (WS-57
 * TR-5, spec `task_references.md` §6 TR-5).
 *
 * The owner asked that `/projects?project=<id>` in a description draw as a
 * pill, as `#123` does. It opens the project, and it adds no link to the task:
 * a task already belongs to one project.
 *
 * The pill names the project by its name when the member can see it
 * (`GET /projects/nodes/{id}`, through the one read cache). Until the name
 * comes, and when the read fails, it shows the link's own words, or
 * "Project" when the words are the bare URL. A project the member cannot see
 * answers 404, so the pill never shows a name from behind a grant.
 */

import EntityPill from "@/components/ui/EntityPill";
import { projectHref } from "@/lib/entityIndex";
import { useCachedResource } from "@/lib/useCachedResource";

interface NodeName {
  name?: string | null;
}

/** The words the pill shows. Pure. Exported for its test. */
export function projectPillLabel(name: string | null | undefined, text: string, href?: string): string {
  if (name) return name;
  const words = text.trim();
  if (!words || words === href || /^https?:\/\//i.test(words) || words.startsWith("/projects")) {
    return "Project";
  }
  return words;
}

export default function ProjectLinkPill({
  id,
  text,
  href,
}: {
  id: string;
  /** The link's own words. */
  text: string;
  href?: string;
}) {
  const node = useCachedResource<NodeName>(`/api/projects/nodes/${id}`, async () => {
    const res = await fetch(`/api/projects/nodes/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(`project: ${res.status}`);
    return (await res.json()) as NodeName;
  });
  return (
    <EntityPill kind="project" label={projectPillLabel(node.data?.name, text, href)} href={projectHref(id)} />
  );
}
