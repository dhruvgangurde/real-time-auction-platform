// The replay card's price label and lead sentence. While the replay plays, the room's present
// tense ("Current bid", "leads with"); once it has reached the close, the outcome ("Final bid",
// "won with", or no sale). A card with no bid yet shows the starting price.
import { landingEnIN } from '../strings/landing-en-IN';

const t = landingEnIN.board;

export function cardWords(
  latest: { alias: string; seq: number } | null,
  finished: boolean,
  sold: boolean,
  startPrice: string,
): { label: string; lead: string } {
  if (latest === null) return { label: t.startingPrice, lead: finished ? t.noSale : t.noBids(startPrice) };
  if (!finished) return { label: t.currentBid, lead: t.leads(latest.alias, latest.seq) };
  return { label: t.finalBid, lead: sold ? t.won(latest.alias, latest.seq) : t.noSale };
}
