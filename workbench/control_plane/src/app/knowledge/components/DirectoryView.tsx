"use client";

/**
 * A directory (WS-61 KC-8b, `knowledge_curation.md` §6): the entities of one
 * type that the member may see, at `/knowledge/directories/<type>`.
 *
 * Each row is one record of an app (a person, a customer, a project) or of
 * Knowledge (a product, a topic). A row with a page links to it. A row with
 * none offers "Write the page", which opens the one form for that act. A row
 * of another app also links to its record there.
 *
 * The search box asks the gateway (`?q=`), which matches the name and the
 * aliases before it cuts the list. The words stay in the address. The view
 * holds no rule about who may see a row: the gateway lists only what the
 * owner app allows.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  entitiesKey,
  listEntities,
  ownerApp,
  ownerHref,
  typeHelp,
  typeIcon,
  typeLabel,
  type Entity,
  type EntityList,
  type EntityType,
} from "../lib/entities";
import { ADD_LABEL } from "../lib/entityForm";
import { pageHref } from "../lib/links";
import { useKnowledge } from "./KnowledgeContext";
import WritePageDialog from "./WritePageDialog";

/** The wait before the words reach the server and the address, in ms. */
export const DIRECTORY_DEBOUNCE_MS = 250;

export default function DirectoryView({ type }: { type: EntityType }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const written = params?.get("q") ?? "";
  const [raw, setRaw] = useState(written);
  const [q, setQ] = useState(written.trim());
  const [writing, setWriting] = useState<Entity | null>(null);
  // KC-8d: Knowledge makes a product and a topic, and no other type. Hidden for
  // a member with no space to write in.
  const { openAddEntity, canAddEntity, requestLeave } = useKnowledge();
  const addType = type === "product" || type === "topic" ? type : null;
  const add =
    addType && canAddEntity
      ? { label: ADD_LABEL[addType], icon: "Plus", onClick: () => requestLeave(() => openAddEntity(addType)) }
      : null;

  // The words reach the server and the address after one wait, so neither
  // gets a request for each letter. `replace`, so Back leaves in one step.
  useEffect(() => {
    const next = raw.trim();
    if (next === q) return;
    const timer = setTimeout(() => {
      setQ(next);
      router.replace(next ? `${pathname}?q=${encodeURIComponent(next)}` : pathname, { scroll: false });
    }, DIRECTORY_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [raw, q, router, pathname]);

  const read = useCachedResource<EntityList>(entitiesKey(type, q), () => listEntities(type, q));
  const list = read.data;
  const rows = list?.entities ?? [];

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-directory={type}>
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
        <header className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Icon name={typeIcon(type)} size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-foreground">{typeLabel(type)}</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">{typeHelp(type)}</p>
          </div>
          {add ? (
            <Button variant="primary" size="sm" icon={add.icon} onClick={add.onClick}>
              {add.label}
            </Button>
          ) : null}
        </header>

        <div className="mt-4">
          <Input
            icon="Search"
            type="text"
            role="searchbox"
            enterKeyHint="search"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && raw) {
                e.preventDefault();
                setRaw("");
              }
            }}
            placeholder={`Find in ${typeLabel(type)}`}
            aria-label={`Find in ${typeLabel(type)}`}
            autoComplete="off"
            spellCheck={false}
            className="w-full"
          />
        </div>

        <section className="mt-4" aria-label={typeLabel(type)}>
          {read.loading && !list ? (
            <SkeletonRows count={4} />
          ) : read.error && !list ? (
            <EmptyState
              icon="CloudOff"
              message="This list did not load."
              hint="The server did not answer. Try again in a moment."
              action={{ label: "Try again", icon: "RefreshCw", onClick: read.refresh }}
            />
          ) : rows.length === 0 ? (
            q ? (
              <EmptyState icon="SearchX" message="No match" hint={`Nothing in ${typeLabel(type)} matches these words.`} />
            ) : (
              <EmptyState icon={typeIcon(type)} message="Nothing here yet" action={add ?? undefined} />
            )
          ) : (
            <>
              {list?.truncated ? (
                <p role="status" className="mb-2 text-xs text-muted-foreground" data-directory-truncated="">
                  Showing the first {rows.length}. Search to narrow the list.
                </p>
              ) : null}
              <ul className="divide-y divide-border rounded-lg border border-border bg-card">
                {rows.map((entity) => (
                  <DirectoryRow key={`${entity.ref_table}:${entity.ref_id}`} entity={entity} onWrite={setWriting} />
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
      <WritePageDialog type={type} entity={writing} onClose={() => setWriting(null)} onStale={read.refresh} />
    </div>
  );
}

function DirectoryRow({ entity, onWrite }: { entity: Entity; onWrite: (entity: Entity) => void }) {
  const owner = ownerHref(entity);
  const app = ownerApp(entity.type);
  return (
    <li
      className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-3"
      data-entity-ref={entity.ref_id}
    >
      <span className="min-w-0 flex-1">
        {entity.page_id ? (
          <Link
            href={pageHref(entity.page_id)}
            className="block truncate text-sm text-foreground tech-transition hover:underline"
            title={entity.name}
          >
            {entity.name}
          </Link>
        ) : (
          <span className="block truncate text-sm text-foreground" title={entity.name}>
            {entity.name}
          </span>
        )}
      </span>
      <span className="flex shrink-0 flex-wrap items-center gap-2">
        {owner && app ? (
          <Button size="sm" variant="ghost" icon="ExternalLink" href={owner}>
            Open in {app}
          </Button>
        ) : null}
        {entity.page_id ? (
          <Button size="sm" variant="secondary" icon="FileText" href={pageHref(entity.page_id)}>
            Page
          </Button>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            icon="FilePlus"
            aria-label={`Write the page for ${entity.name}`}
            onClick={() => onWrite(entity)}
          >
            Write the page
          </Button>
        )}
      </span>
    </li>
  );
}
