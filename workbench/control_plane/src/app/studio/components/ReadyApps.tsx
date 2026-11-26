"use client";

/**
 * "Start from a ready app" (STU-K1b, rung 1 of the Studio ladder).
 *
 * It lists the templates of the gateway with one "Use this app" button each.
 * The click makes the app in one step and opens it. The whole section hides
 * when Studio is off for the org (404) and when the list is empty, so nothing
 * shows until the backend flag is on.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Button from "@/components/ui/Button";
import { loadTemplatesView, startFromTemplate, type AppTemplate } from "@/lib/studio/templatesApi";

export default function ReadyApps() {
  const router = useRouter();
  const [templates, setTemplates] = useState<AppTemplate[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void loadTemplatesView().then((v) => {
      if (live && v.kind === "ok") setTemplates(v.templates);
    });
    return () => {
      live = false;
    };
  }, []);

  if (templates.length === 0) return null;

  async function pick(id: string) {
    if (pending) return;
    setPending(id);
    setError(null);
    const r = await startFromTemplate(id);
    if (r.ok) router.push(r.href);
    else setError(r.message);
    setPending(null);
  }

  return (
    <section aria-label="Start from a ready app" className="rounded-xl border border-border bg-card overflow-hidden shrink-0 flex flex-col">
      <div className="px-4 sm:px-6 py-3 border-b border-border">
        <h2 className="text-sm font-bold text-foreground">Start from a ready app</h2>
      </div>
      {error && <p role="alert" className="px-4 sm:px-6 pt-3 text-sm text-destructive">{error}</p>}
      <ul className="flex flex-col divide-y divide-border">
        {templates.map((t) => (
          <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 sm:px-6 py-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">{t.name}</p>
              <p className="text-xs text-muted-foreground">{t.purpose}</p>
            </div>
            <Button
              size="md"
              onClick={() => void pick(t.id)}
              loading={pending === t.id}
              disabled={pending !== null}
            >
              Use this app
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
