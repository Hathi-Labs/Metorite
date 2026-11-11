/**
 * The square that names a provider in the connect flow (WS-17 EM-G11a item 8).
 *
 * The provider list, the range step and the disclosure step each draw it, so
 * one provider has one glyph in all three.
 *
 * A provider with a `mark` draws that company's own file on a neutral tile.
 * A tint of the member's accent behind Google's "G" would recolour the mark,
 * and Google's rules forbid that. Any other provider draws its icon on the
 * house tint. The mark keeps its colours when the row is disabled.
 */

import Icon from "@/components/Icon";
import { CONNECT_PROVIDERS, type ConnectProviderId } from "../lib/connect";

const TILE = "flex h-9 w-9 shrink-0 items-center justify-center rounded-md";

export function ProviderGlyph({
  provider,
  available = true,
}: {
  provider: ConnectProviderId;
  /** False draws the muted tile of a disabled row. */
  available?: boolean;
}) {
  const p = CONNECT_PROVIDERS.find((c) => c.id === provider);
  if (p?.mark) {
    return (
      <span className={`${TILE} border border-border bg-card`}>
        <img src={p.mark} alt="" width={18} height={18} />
      </span>
    );
  }
  return (
    <span className={`${TILE} ${available ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}`}>
      <Icon name={p?.icon ?? "Mail"} size={18} />
    </span>
  );
}
