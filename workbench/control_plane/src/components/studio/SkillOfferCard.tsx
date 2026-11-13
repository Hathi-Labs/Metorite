"use client";

/**
 * SkillOfferCard — "Save this as a skill?" after a finished chat turn
 * (STU-F6a, `project-docs/specs/ai_studio.md` §14 F6a).
 *
 * The member asked for a task, and the assistant did it in three or more
 * steps. The card shows the skill that Save makes: its name, its
 * description, its instructions and each action with its class. Save makes
 * one private draft and then links to it in Studio. Not now closes the card.
 *
 * Every rule lives in `lib/studio/skillOffer.ts`, and this file only draws
 * it. The card makes no model call. It reads the catalog through the one
 * read cache, and only for a turn that passes the rules that need no
 * catalog. A catalog that does not load (404 while Studio is off for the
 * org, 401, 403 or a fault) draws nothing.
 *
 * Dark behind `NEXT_PUBLIC_SKILL_OFFER` (`lib/studio/skillOfferFlag.ts`).
 * Mounted by `StudioToolCards`, in the one `MessageBubble` mount, so it
 * shows in `/chat`, in the dock and on a phone. Fences:
 * `SkillOfferCard.test.ts`, `lib/studio/skillOffer.test.ts` and
 * `lib/chatCardConformance.test.ts`.
 */

import { useSyncExternalStore, type ReactNode } from "react";

import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import {
  OFFER_LINE,
  OFFER_TITLE,
  SAVED_LINE,
  SAVE_HINT,
  answeredOffer,
  offerAsks,
  offerLocal,
  pressDismiss,
  pressSave,
  skillHref,
  skillOffer,
  subscribeOffers,
  type OfferLocal,
  type OfferTurn,
  type SkillOffer,
} from "@/lib/studio/skillOffer";
import { classWord } from "@/lib/studio/skillForm";
import { skillOfferOn } from "@/lib/studio/skillOfferFlag";
import { createSkill, listCatalogActions, type CatalogAction } from "@/lib/studio/skillsApi";
import { useCachedResource } from "@/lib/useCachedResource";

/** The cache key of the catalog that the offer reads. */
export const OFFER_CATALOG_KEY = "studio:skill-offer:catalog";
/** The space of the card in the turn, after the other tool cards. */
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

/** The offer: the draft that Save sends, and the two buttons. */
export function SkillOfferView({ offer, local, onSave, onDismiss }: {
  offer: SkillOffer;
  local: OfferLocal;
  onSave: () => void;
  onDismiss: () => void;
}) {
  const saving = local.kind === "saving";
  const error = local.kind === "open" ? local.error : null;
  const { draft, actions } = offer;
  return (
    <RollupCard id={`skill-offer:${offer.messageId}`} title={OFFER_TITLE} icon="Sparkles" header="own">
      <section aria-label={OFFER_TITLE} data-skill-offer={offer.messageId} className={CARD_FRAME_CLASS}>
        <RollupHeader />
        <RollupBody className="space-y-2 pt-1.5">
          <p className="break-words text-xs text-muted-foreground">{OFFER_LINE}</p>
          <dl className="space-y-1 rounded-md border border-border bg-background/60 px-2.5 py-2" data-skill-offer-draft="">
            <Field label="Name">{draft.name}</Field>
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
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" icon="Save" loading={saving} disabled={saving} onClick={onSave} data-skill-offer-save="">
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button variant="secondary" size="sm" disabled={saving} onClick={onDismiss} data-skill-offer-dismiss="">
              Not now
            </Button>
            {error ? (
              <span role="alert" className="min-w-0 break-words text-[10px] text-destructive" data-skill-offer-error="">
                {error}
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

  if (!on) return null;
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
        onSave={() => void pressSave(offer, createSkill)}
        onDismiss={() => pressDismiss(offer.messageId)}
      />
    </div>
  );
}
