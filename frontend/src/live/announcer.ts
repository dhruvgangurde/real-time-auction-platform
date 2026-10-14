// Live-region text (brief 11.4), from a diff of two room states: polite for room-wide changes,
// assertive only for the viewer's own outcomes. The strings are exactly those listed per state
// in design/shots/03-room/index.md. Polite text goes through the app's one queue (lib/polite.ts):
// at most one announcement every 3 s, always the latest.
import { formatSpan, formatWindow } from '../lib/clock';
import { formatINR } from '../lib/money';
import type { Catalogue } from '../strings/catalogue';
import type { RoomState } from './room';

export type Announcement = { polite?: string; assertive?: string };

export type AnnounceDeps = {
  catalogue: Catalogue;
  /** a UTC instant as HH:MM:SS in the viewer's zone (clock.ts formatLocal with seconds) */
  time: (ms: number) => string;
  /** the auction's soft-close window (detail antisnipe_threshold_s), null until known */
  windowMs: () => number | null;
};

export function announce(prev: RoomState, next: RoomState, deps: AnnounceDeps): Announcement {
  const { catalogue: s, time } = deps;
  const a = s.announce;
  const out: Announcement = {};
  const polite = (text: string) => {
    out.polite = text;
  };
  const assertive = (text: string) => {
    out.assertive = text;
  };
  const price = (n: number | null) => (n === null ? '' : formatINR(n));

  // own outcomes (assertive), in the order they can happen in one step
  if (next.last !== prev.last && next.last !== null) {
    const last = next.last;
    if (last.kind === 'accepted') assertive(s.room.accepted(price(last.amount)));
    else if (last.kind === 'armed') assertive(a.maxSet(price(last.maximum), next.own.leading));
    else if (last.kind === 'rejected' && last.code !== 'SERVICE_UNAVAILABLE') {
      const reason = (s.errors as Record<string, string | undefined>)[last.code] ?? s.unknownError;
      const minimum = last.minNext !== null ? ` ${a.minimumNow(price(last.minNext))}` : '';
      assertive(a.notPlaced(reason) + minimum);
    }
  }
  if (next.own.outbidAt !== null && next.own.outbidAt !== prev.own.outbidAt) {
    assertive(
      prev.own.maximumActive && prev.own.maximum !== null
        ? a.maxReached(price(prev.own.maximum), price(next.own.outbidAt), price(next.minNext))
        : s.room.outbid(price(next.own.outbidAt), price(next.minNext)),
    );
  }
  if (next.own.result === 'won' && prev.own.result !== 'won') assertive(a.won(price(next.finalPrice ?? next.price)));

  // room-wide changes (polite): the latest wins when several happen in one step
  if (next.closedCode === 4004 && prev.closedCode !== 4004) polite(a.signedOut);
  if (next.connection === 'reconnecting' && prev.connection !== 'reconnecting') polite(a.reconnecting);
  if (next.degraded && !prev.degraded) polite(a.degraded);
  if (next.status === 'live' && prev.status === 'scheduled' && next.endTimeMs !== null) {
    polite(a.open(price(next.startPrice), time(next.endTimeMs)));
  }
  // a change of leading alone is the viewer's own outcome (assertive above), not a room change
  const priceChanged = next.price !== prev.price || next.leaderAlias !== prev.leaderAlias;
  if (next.terminal === null && next.price !== null && (priceChanged || prev.status === null)) {
    polite(a.price(price(next.price), next.leaderAlias, next.own.leading, next.reserveMet));
  }
  if (next.extensionCount > prev.extensionCount && next.endTimeMs !== null && next.previousEndMs !== null) {
    const span = formatSpan(next.endTimeMs - next.previousEndMs, s.roomUi.units);
    const window = formatWindow(deps.windowMs() ?? 60_000, s.roomUi.units);
    polite(next.finalExtension ? a.capReached(span, time(next.endTimeMs)) : a.extended(span, window, next.extensionCount, time(next.endTimeMs)));
  }
  if (next.status === 'closing' && prev.status !== 'closing' && next.endTimeMs !== null) polite(a.closing(time(next.endTimeMs)));
  if (next.terminal !== null && prev.terminal === null) {
    if (next.terminal === 'closed_sold') {
      if (next.own.result !== 'won') polite(a.sold(price(next.finalPrice ?? next.price)));
    } else if (next.terminal === 'closed_no_sale') polite(a.noSale);
    else polite(a.cancelled);
  }
  return out;
}
