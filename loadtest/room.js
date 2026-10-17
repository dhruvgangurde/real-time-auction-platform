// The load test's room (Phase 11, loadtest/README.md), against the dev stack through the web
// service. AS-4: one auction, 100 and more connected bidders, bidding concentrated in the final
// minute. Every bidder is one minted account (loadtest-setup) with one socket: it authenticates,
// subscribes, then bids the minimum it last saw, holding back while it leads, one bid in flight
// at a time; every 2 s (jittered) in the steady phase, every 1 s in the final minute. Each
// bidder times its own bids (its first one anywhere in the first gap after the opening), so
// bids reach the server spread out, as independent people's would: the first run's shared
// 50 ms tick fired in step across bidders and sent them in bursts (docs/decisions.md). A reader
// requests listings, search and detail at a constant rate meanwhile (NFR-6).
//
// Measured here (client side): the acknowledgement time; NFR-3 (each room event's receipt minus
// its server_time_ms, stamped by the publishing instance) at every socket; NFR-2 for one's own
// bids (send to one's own bid_placed); REST durations per endpoint (NFR-6). Logged for the
// report: every acknowledged bid (NFR-28 check 6, NFR-2's join) and every rejected one, with its
// send time (NFR-4: which bids could still have won when sent), every bid_placed at every fifth
// socket (NFR-2 at other subscribers), and each socket's deliveries and replies at the end (NFR-5).
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import http from 'k6/http';
import { Counter, Trend } from 'k6/metrics';
import { clearTimeout, setTimeout } from 'k6/timers';
import { WebSocket } from 'k6/websockets';

const PLAN = JSON.parse(open('/loadtest/.run/plan.json'));
const BIDDERS = new SharedArray('bidders', () => JSON.parse(open('/loadtest/.run/plan.json')).bidders);
const WS_URL = __ENV.WS_URL || 'ws://web/api/v1/ws';
const HTTP_URL = __ENV.HTTP_URL || 'http://web';
const AUCTION = PLAN.auction_id;
const STEADY_GAP_MS = 2000;
const FINAL_GAP_MS = 1000;
const FINAL_MINUTE_MS = 60000;
const REPLY_TIMEOUT_MS = 5000;
const RECHECK_MS = 50; // due while leading or awaiting a reply: look again in 25 to 75 ms
// every fifth socket logs every bid_placed it receives (NFR-2's join); both parities of the
// connection order, so both instances behind the round robin
const SAMPLE_EVERY = 5;
const BROADCASTS = new Set(['bid_placed', 'timer_extended', 'auction_started', 'auction_closed']);
const LAST_END_MS = PLAN.end_ms + 10 * PLAN.soft_close_s * 1000; // with every extension (FR-54)
const DEADLINE_MS = LAST_END_MS + 90000; // the sockets' last moment: settled and broadcast by then

const now0 = Date.now();
if (PLAN.expires_at_ms <= DEADLINE_MS + 10000) {
  throw new Error('the tokens would expire before the run ends: run loadtest-setup again');
}
const seconds = (ms) => `${Math.max(1, Math.ceil(ms / 1000))}s`;

export const options = {
  scenarios: {
    bidders: {
      executor: 'per-vu-iterations',
      vus: BIDDERS.length,
      iterations: 1,
      maxDuration: seconds(DEADLINE_MS - now0 + 30000),
      exec: 'bidder',
    },
    reads: {
      executor: 'constant-arrival-rate',
      rate: 6, // two of each request a second
      timeUnit: '1s',
      startTime: seconds(PLAN.start_ms - now0),
      duration: seconds(LAST_END_MS - PLAN.start_ms),
      preAllocatedVUs: 6,
      maxVUs: 20,
      exec: 'reader',
    },
  },
  summaryTrendStats: ['min', 'med', 'avg', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
};

const ackMs = new Trend('bid_ack_ms', true);
const ownBroadcastMs = new Trend('nfr2_own_broadcast_ms', true);
const deliveryMs = new Trend('nfr3_delivery_ms', true);
const sent = new Counter('bids_sent');
const accepted = new Counter('bids_accepted');
const subscribed = new Counter('sockets_subscribed');
const closedSeen = new Counter('sockets_saw_auction_closed');
const restMs = {
  listing: new Trend('rest_listing_ms', true),
  search: new Trend('rest_search_ms', true),
  detail: new Trend('rest_detail_ms', true),
};

export function bidder() {
  const index = exec.scenario.iterationInTest; // 0 .. bidders - 1, one per VU
  const me = BIDDERS[index];
  const sample = index % SAMPLE_EVERY === SAMPLE_EVERY - 1;
  const ws = new WebSocket(WS_URL);
  const received = new Set();
  const pending = {}; // request_id -> {amount, sent}: a late reply is still matched
  const ownSent = {}; // sequence_no -> send time, until one's own bid_placed arrives
  const tally = { sent: 0, accepted: 0, rejected: {}, timeouts: 0, errors: {} };
  let duplicates = 0;
  let isSubscribed = false;
  let fromSeq = null; // the snapshot's sequence: every later bid must arrive as a bid_placed
  let live = false;
  let closed = false;
  let finalSeq = null;
  let minNext = PLAN.start_price;
  let increment = PLAN.min_increment;
  let lastSeq = 0;
  let mySeq = 0;
  let endMs = PLAN.end_ms;
  let inflight = null; // the request_id awaiting its reply
  let bidTimer = null; // this bidder's own timer for its next bid
  let replyTimer = null;
  let ended = false;

  const send = (message) => ws.send(JSON.stringify(message));
  const subscribe = () => send({ type: 'subscribe', auction_id: AUCTION });
  const leading = () => mySeq > 0 && mySeq === lastSeq;
  const bump = (counts, key) => {
    counts[key] = (counts[key] || 0) + 1;
  };
  const advance = (seq, amount) => {
    if (seq > lastSeq) {
      lastSeq = seq;
      minNext = amount + increment;
    }
  };

  const stop = (timer) => {
    if (timer !== null) clearTimeout(timer);
  };
  const finish = () => {
    if (ended) return;
    ended = true;
    stop(bidTimer);
    stop(replyTimer);
    stop(deadline);
    let missing = null;
    if (finalSeq !== null && fromSeq !== null) {
      missing = 0;
      for (let s = fromSeq + 1; s <= finalSeq; s++) if (!received.has(s)) missing++;
    }
    console.log(JSON.stringify({
      e: 'rx_summary', vu: index, bidder: me.id, subscribed: isSubscribed, from_seq: fromSeq,
      distinct: received.size, duplicates, max_seq: lastSeq, saw_closed: closed,
      final_seq: finalSeq, missing, ...tally,
    }));
    ws.close();
  };

  const gapMs = () => (Date.now() >= endMs - FINAL_MINUTE_MS ? FINAL_GAP_MS : STEADY_GAP_MS);
  const arm = (ms) => {
    stop(bidTimer);
    bidTimer = setTimeout(attempt, ms);
  };
  const attempt = () => {
    bidTimer = null;
    if (ended || closed || !isSubscribed || !live) return;
    if (inflight !== null || leading()) return arm(RECHECK_MS * (0.5 + Math.random()));
    tally.sent += 1;
    const rid = `b${index}-${tally.sent}`;
    inflight = rid;
    pending[rid] = { amount: minNext, sent: Date.now() };
    sent.add(1);
    send({ type: 'place_bid', auction_id: AUCTION, amount: minNext, request_id: rid });
    stop(replyTimer);
    replyTimer = setTimeout(() => {
      if (inflight === rid) {
        tally.timeouts += 1;
        inflight = null;
      }
    }, REPLY_TIMEOUT_MS);
    arm(gapMs() * (0.75 + Math.random() * 0.5));
  };
  const goLive = () => {
    if (live) return;
    live = true;
    arm(Math.random() * gapMs()); // the first bid anywhere in the first gap, not all at once
  };
  const deadline = setTimeout(finish, Math.max(0, DEADLINE_MS - Date.now()));

  // the reply to a bid of ours, matched by request_id even after its timeout
  const reply = (f) => {
    const p = pending[f.request_id];
    if (p === undefined) return null;
    delete pending[f.request_id];
    if (f.request_id === inflight) inflight = null;
    return p;
  };

  ws.onopen = () => send({ type: 'authenticate', token: me.token });
  ws.onerror = (e) => console.error(JSON.stringify({ e: 'socket_error', vu: index, error: String(e.error) }));
  ws.onclose = () => finish();
  ws.onmessage = (event) => {
    const now = Date.now();
    const f = JSON.parse(event.data);
    if (BROADCASTS.has(f.type)) deliveryMs.add(now - f.server_time_ms);
    switch (f.type) {
      case 'authenticated':
        if (!isSubscribed) subscribe();
        break;
      case 'subscribed': {
        const snap = f.snapshot;
        if (!isSubscribed) fromSeq = snap.sequence_no;
        isSubscribed = true;
        subscribed.add(1);
        increment = snap.min_increment;
        minNext = snap.min_next;
        lastSeq = snap.sequence_no;
        endMs = snap.end_time_ms;
        if (snap.status === 'live') goLive();
        break;
      }
      case 'auction_started':
        goLive();
        break;
      case 'bid_placed': {
        if (received.has(f.sequence_no)) duplicates++;
        received.add(f.sequence_no);
        advance(f.sequence_no, f.amount);
        if (f.sequence_no >= lastSeq) endMs = f.end_time_ms;
        if (ownSent[f.sequence_no] !== undefined) {
          ownBroadcastMs.add(now - ownSent[f.sequence_no]);
          delete ownSent[f.sequence_no];
        }
        if (sample) console.log(JSON.stringify({ e: 'rx', vu: index, seq: f.sequence_no, t: now, st: f.server_time_ms }));
        break;
      }
      case 'timer_extended':
        endMs = f.end_time_ms;
        break;
      case 'bid_accepted': {
        const p = reply(f);
        if (p === null) break;
        ackMs.add(now - p.sent);
        accepted.add(1);
        tally.accepted += 1;
        mySeq = Math.max(mySeq, f.sequence_no);
        if (!received.has(f.sequence_no)) ownSent[f.sequence_no] = p.sent;
        advance(f.sequence_no, f.amount);
        console.log(JSON.stringify({
          e: 'ack', vu: index, seq: f.sequence_no, bidder: me.id, amount: f.amount, sent: p.sent, acked: now,
        }));
        break;
      }
      case 'bid_rejected': {
        const p = reply(f);
        if (p === null) break;
        bump(tally.rejected, f.code);
        if (f.min_next) minNext = Math.max(minNext, f.min_next);
        console.log(JSON.stringify({ e: 'rej', vu: index, amount: p.amount, sent: p.sent, code: f.code }));
        break;
      }
      case 'auction_closed':
        if (!closed) {
          closedSeen.add(1);
          setTimeout(finish, 3000); // a late bid_placed from the other instance can still arrive
        }
        closed = true;
        finalSeq = f.final_sequence_no;
        break;
      case 'error':
        bump(tally.errors, f.code);
        if (!isSubscribed && f.code === 'SERVICE_UNAVAILABLE') {
          setTimeout(subscribe, 250); // the lot going live: the client contract says retry
        } else {
          console.error(JSON.stringify({ e: 'error_frame', vu: index, code: f.code }));
        }
        break;
      default:
        break;
    }
  };
}

const READS = [
  { name: 'listing', path: '/api/v1/auctions?status=live&page_size=20' },
  { name: 'search', path: '/api/v1/auctions?q=load&status=live&status=scheduled' },
  { name: 'detail', path: `/api/v1/auctions/${AUCTION}` },
];

export function reader() {
  const read = READS[exec.scenario.iterationInTest % READS.length];
  const response = http.get(`${HTTP_URL}${read.path}`, { tags: { name: read.name } });
  restMs[read.name].add(response.timings.duration);
  check(response, { 'REST read answered 200': (r) => r.status === 200 });
}

export function handleSummary(data) {
  const out = { '/loadtest/.run/summary.json': JSON.stringify(data, null, 1) };
  const m = data.metrics;
  const line = (name, metric) => (metric ? `${name}: p95 ${metric.values['p(95)'].toFixed(1)} ms\n` : '');
  out.stdout =
    `k6 finished: ${(m.bids_accepted || { values: { count: 0 } }).values.count} bids accepted\n` +
    line('acknowledgement', m.bid_ack_ms) +
    line('NFR-3 delivery', m.nfr3_delivery_ms) +
    line('own broadcast', m.nfr2_own_broadcast_ms);
  return out;
}
