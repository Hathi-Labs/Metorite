/**
 * /knowledge — the Knowledge app (WS-61, `knowledge_base.md`).
 *
 * KB-0 is the scaffold: the app bar with the name as the one `<h1>`, and a
 * calm empty body. The pane is `preview`, so the route renders by URL and
 * the nav entry stays hidden. `feature:knowledge` gates it (`lib/access.ts`).
 *
 * It mounts no ⌘K listener, no palette, no bell and no assistant rail. The
 * shell owns those (`AGENTS.md` rule 10). It calls no API and reads no flag.
 * KB-2a brings the Spaces view, and §11.1 of the spec holds the surface.
 */

import { AppTopBar } from "@/components/AppTopBar";
import { EmptyState } from "@/components/EmptyState";

export default function KnowledgePage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <AppTopBar title="Knowledge" />
      <div className="flex min-h-0 flex-1 flex-col overflow-auto">
        <EmptyState icon="BookOpen" message="Pages, spaces and connected folders arrive here." />
      </div>
    </div>
  );
}
