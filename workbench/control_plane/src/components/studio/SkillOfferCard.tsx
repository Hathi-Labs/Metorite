"use client";

/**
 * SkillOfferCard — "Save this as a skill?" after a finished chat turn
 * (STU-F6a, `project-docs/specs/ai_studio.md` §14 F6a).
 *
 * The member asked for a task, and the assistant did it in three or more
 * steps. The card shows the skill that Save makes: a name that the member
 * can change, the description, the instructions and each action with its
 * class. Save makes one private draft and then links to it in Studio. Not
 * now closes the card. A refusal that the card cannot repair closes the
 * offer, and the card links to the skill creator of Studio.
 *
 * Every rule lives in `lib/studio/skillOffer.ts`, and this file only draws
 * it. The card makes no model call. It reads the catalog through the one
 * read cache, and only for a turn that passes the rules that need no
 * catalog. A catalog that does not load (404 while Studio is off for the
 * org, 401, 403 or a fault) draws nothing.
 *
 * Dark behind `NEXT_PUBLIC_SKILL_OFFER` (`lib/studio/skillOfferFlag.ts`).
 * `MessageBubble` mounts it once, after the action bar of the turn. The card
 * comes after the turn ends, so its place at the end moves no other part of
 * the turn. A long body is cut by `ShowMore`, and the card holds no scroll
 * box. Fences: `SkillOfferCard.test.ts`, `lib/studio/skillOffer.test.ts` and
 * `lib/chatCardConformance.test.ts`.
 */

import { useSyncExternalStore, type ReactNode } from "react";

import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import { Input } from "@/components/ui/Input";
import ShowMore from "@/components/ui/ShowMore";
import {
  OFFER_LINE,
  OFFER_TITLE,
  SAVED_LINE,
  SAVE_HINT,
  STUDIO_NEW_SKILL,
  answeredOffer,
  boxName,
  clearOfferError,
  offerAsks,
  offerLocal,
  pressDismiss,
  pressSave,
  setTypedName,
  skillHref,
  skillOffer,
  subscribeOffers,
  typedName,
  type OfferLocal,
  type OfferTurn,
  type SkillOffer,
} from "@/lib/studio/skillOffer";
import { classWord } from "@/lib/studio/skillForm";
import { skillOfferOn } from "@/lib/studio/skillOfferFlag";
import { NAME_MAX } from "@/lib/studio/skillStarter";
import { createSkill, listCatalogActions, type CatalogAction } from "@/lib/studio/skillsApi";
import { useCachedResource } from "@/lib/useCachedResource";

/** The cache key of the catalog that the offer reads. */
export const OFFER_CATALOG_KEY = "studio:skill-offer:catalog";
/** The space of the card at the end of the turn, under the action bar. */
const SLOT_CLASS = "mt-3 min-w-0";

/**
 * The catalog, or null when it draws no card: a 404 (Studio is off for the
 * org), a 401, a 403 and any fault. The offer is optional, so a fault shows
 * no error in the chat.
 */
export async function loadOfferCatalog(): Promise<CatalogAction[] | null> {
  const reply = await listCatalogActions();
  return reply.ok ? (reply.data.actions ?? []) : null;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 text-xs">
      <dt className="w-24 shrink-0 pt-px text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground">{children}</dd>
    </div>
  );
}

/** The offer: the draft that Save sends, the name box and the two buttons. */
export function SkillOfferView({ offer, local, name, onName, onSave, onDismiss, onReload }: {
  offer: SkillOffer;
  local: OfferLocal;
  name: string;
  onName: (name: string) => void;
  onSave: () => void;
  onDismiss: () => void;
  onReload: () => void;
}) {
  const saving = local.kind === "saving";
  const error = local.kind === "open" ? local.error : null;
  const nameId = `skill-offer-name-${offer.messageId}`;
  const { draft, actions } = offer;
  return (
    <RollupCard id={`skill-offer:${offer.messageId}`} title={OFFER_TITLE} icon="Sparkles" header="own">
      <section aria-label={OFFER_TITLE} data-skill-offer={offer.messageId} className={CARD_FRAME_CLASS}>
        <RollupHeader />
        <RollupBody className="space-y-2 pt-1.5">
          <p className="break-words text-xs text-muted-foreground">{OFFER_LINE}</p>
          <div className="flex flex-col gap-1">
            <label htmlFor={nameId} className="text-xs text-muted-foreground">Name</label>
            <Input
              id={nameId}
              inputSize="sm"
              value={name}
              maxLength={NAME_MAX}
              disabled={saving}
              aria-invalid={error?.place === "name" || undefined}
              aria-describedby={error?.place === "name" ? `${nameId}-error` : undefined}
              onChange={(e) => onName(e.target.value)}
              data-skill-offer-name=""
            />
            {error?.place === "name" && (
              <p role="alert" id={`${nameId}-error`} className="text-xs text-destructive" data-skill-offer-error="">
                {error.text}
              </p>
            )}
          </div>
          <ShowMore>
            <dl className="space-y-1 rounded-md border border-border bg-background/60 px-2.5 py-2" data-skill-offer-draft="">
              <Field label="Description">{draft.description}</Field>
              <Field label="Instructions">{draft.body.instructions}</Field>
              <Field label="Actions">
                <ol className="space-y-1">
                  {actions.map((a) => (
                    <li key={a.id} className="flex items-start gap-2" data-skill-offer-action={a.id}>
                      <span className="min-w-0 flex-1 break-words">{a.title}</span>
                      {/* The class sits at the right, as in the Studio form. */}
                      <Badge tone="neutral" size="xs" className="shrink-0">{classWord(a.cls)}</Badge>
                    </li>
                  ))}
                </ol>
              </Field>
            </dl>
          </ShowMore>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" icon="Save" loading={saving} disabled={saving} onClick={onSave} data-skill-offer-save="">
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button variant="secondary" size="sm" disabled={saving} onClick={onDismiss} data-skill-offer-dismiss="">
              Not now
            </Button>
            {error?.place === "stale" && (
              <Button variant="ghost" size="sm" icon="RefreshCw" onClick={onReload} data-skill-offer-reload="">
                Reload the actions
              </Button>
            )}
            {error && error.place !== "name" ? (
              <span role="alert" className="min-w-0 break-words text-[10px] text-destructive" data-skill-offer-error="">
                {error.text}
              </span>
            ) : (
              <span className="text-[10px] text-muted-foreground">{SAVE_HINT}</span>
            )}
          </div>
        </RollupBody>
      </section>
    </RollupCard>
  );
}

/**
 * The chat cannot save this skill (`offerFailure` place `studio`): the
 * offer is closed, and the one next step is the skill creator of Studio.
 */
export function SkillOfferHandoff({ messageId, text }: { messageId: string; text: string }) {
  return (
    <RollupCard id={`skill-offer:${messageId}`} title={OFFER_TITLE} icon="Sparkles" header="own">
      <section aria-label={OFFER_TITLE} data-skill-offer-studio="" className={CARD_FRAME_CLASS}>
        <RollupHeader />
        <RollupBody className="flex flex-wrap items-center gap-2 pt-1.5">
          <p role="alert" className="min-w-0 break-words text-xs text-muted-foreground">{text}</p>
          <Button variant="secondary" size="sm" icon="ExternalLink" href={STUDIO_NEW_SKILL} data-skill-offer-studio-link="">
            Make this skill in Studio
          </Button>
        </RollupBody>
      </section>
    </RollupCard>
  );
}

/** A saved offer: one line and the link to the draft in Studio. */
export function SkillOfferSaved({ messageId, skillId }: { messageId: string; skillId: string }) {
  return (
    <RollupCard id={`skill-offer:${messageId}`} title={OFFER_TITLE} icon="Sparkles" header="own">
      <section aria-label={OFFER_TITLE} data-skill-offer-saved={skillId} className={CARD_FRAME_CLASS}>
        <RollupHeader />
        <RollupBody className="flex flex-wrap items-center gap-2 pt-1.5">
          <p role="status" className="min-w-0 break-words text-xs text-muted-foreground">{SAVED_LINE}</p>
          <Button variant="secondary" size="sm" icon="ExternalLink" href={skillHref(skillId)} data-skill-offer-open="">
            Open in Studio
          </Button>
        </RollupBody>
      </section>
    </RollupCard>
  );
}

/** The offer of one turn, or nothing (`lib/studio/skillOffer.ts` rules 1 to 5). */
export default function SkillOfferCard({ turn }: { turn: OfferTurn }) {
  const on = skillOfferOn();
  const read = () => offerLocal(turn.messageId);
  const local = useSyncExternalStore(subscribeOffers, read, read);
  const asks = on && offerAsks(turn, answeredOffer(local));
  // The catalog is read only for a turn that asks, so the flag off and most
  // turns send no request.
  const catalog = useCachedResource(asks ? OFFER_CATALOG_KEY : null, loadOfferCatalog);
  // The name box lives in the offer store, so Save reads the typed name itself
  // (`pressSave` reads `boxName`), and a remount keeps it.
  const readName = () => typedName(turn.messageId);
  useSyncExternalStore(subscribeOffers, readName, readName);

  if (!on) return null;
  if (local.kind === "studio") {
    return (
      <div className={SLOT_CLASS}>
        <SkillOfferHandoff messageId={turn.messageId} text={local.text} />
      </div>
    );
  }
  if (local.kind === "saved") {
    return (
      <div className={SLOT_CLASS}>
        <SkillOfferSaved messageId={turn.messageId} skillId={local.skillId} />
      </div>
    );
  }
  const offer = asks ? skillOffer(turn, catalog.data, answeredOffer(local)) : null;
  if (!offer) return null;
  return (
    <div className={SLOT_CLASS}>
      <SkillOfferView
        offer={offer}
        local={local}
        name={boxName(offer)}
        onName={(next) => setTypedName(offer.messageId, next)}
        onSave={() => void pressSave(offer, createSkill)}
        onDismiss={() => pressDismiss(offer.messageId)}
        onReload={() => {
          clearOfferError(offer.messageId);
          catalog.refresh();
        }}
      />
    </div>
  );
}
