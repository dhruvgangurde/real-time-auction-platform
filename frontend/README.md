# Frontend

React + TypeScript (strict) with Vite. The design brief (`docs/design-brief.md`) is the spec; the room's client rules are its section 13 and `docs/architecture.md` section 4.

## Commands

```
nvm use                 # Node 24.19 (.nvmrc); engines refuse other majors
npm ci                  # exact versions from the lockfile
npm run dev             # Vite on 5173, /api and /media proxied to the compose stack (8011)
npm run typecheck       # tsc --noEmit
npm run build           # typecheck, vite build into dist/ (served by the web service on 8011), chunk budget
npm test                # Vitest: reducer, money, clock, strings, contract, client, socket, lint rules
npm run lint            # ESLint, including the project rules below
npm run e2e             # Playwright against http://localhost:8011 only (the real CSP), after npm run build
```

Playwright never runs against the dev server: the content security policy only exists on 8011.

Bring the stack up for e2e with the e2e compose file, which raises only the identity and
anonymous-read rate limits that one host address running the whole suite would otherwise trip
(the default `docker compose up` keeps the production limits):

```
docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.e2e.yml up -d --build
cd frontend && npm run build && npm run e2e
```

The suite runs one worker: `e2e/room-shots.spec.ts` pauses Redis to shoot the degraded state, which must not run beside the other specs.

## Dependencies

Exact versions only (`.npmrc` has `save-exact`). A new version is taken only once it is at least seven days old. Everything in `package.json` is from brief 11.2; nothing else without a one-line reason in `docs/decisions.md`.

## Strings

`src/strings/catalogue.ts` declares the `Catalogue` type: nested groups of strings, functions for interpolation (`room.minNext(amount)`, `nav.unread(n)`), and `errors` and `close` as `Record`s over every documented error and close code, so a missing message fails `tsc`. `src/strings/en-IN.ts` is the English (India) catalogue. Components call `const s = useStrings()` and render `{s.nav.live}`; non-React code takes a `Catalogue` argument. A second locale is one more file that satisfies the type.

## The room: one path, and where every brief-13 rule is tested

`src/live/room.ts` is one pure reducer per room: `roomReducer(state, action)` returns the next state and the effects to run. `src/live/roomController.ts` is the single entry: the initial `/live`, every refetch, the 2 s poll (a tab without the socket), every socket frame (routed by `auction_id`) and every REST bid response become a `RoomAction` through `dispatch()`; the controller runs the effects (refetch, gap check, bid retry, rate-limit timer) and exposes `subscribe`/`getState` for `useSyncExternalStore`. Malformed frames are dropped by `parseFrame` before they reach it.

Reducer tests are in `src/live/room.test.ts`; the others name their file.

| Item | Rule | Test |
|---|---|---|
| 1 | Authenticate first within 5 s; no token in a URL; re-authenticate before expiry | Not a reducer rule. `socket.test.ts`: "authenticate is the first frame and the socket URL carries no token", "re-authenticates with a refreshed token before expires_at_ms" |
| 2 | Close-code rules | Not a reducer rule. `socket.test.ts`: "4002/4004/4012 never reconnects", "4003 refreshes the token, then reconnects at once", "4009/4010 stops with a message instead of looping", "1001/1011/4008/1006 reconnects with exponential backoff and jitter", "the backoff is capped at 30 s", "a reconnect authenticates, resubscribes and resyncs before Reconnected" |
| 3 | One socket per session; other tabs poll 2 s and bid over REST; "Use this tab"; refresh under a lock; token shared, never stored | Not a reducer rule. `tabs.test.ts`: "only the lock holder opens the socket; the other tab polls", "Use this tab steals the lock and the old tab falls back to polling", "when the owner leaves, the waiting tab takes over". `roomController.test.ts`: "a tab without the socket polls /live every 2 s and bids over REST". `auth.test.ts`: "two tabs restoring at once make one refresh call", "a later tab gets the token from a peer and never calls refresh", "a login is shared to the other tabs and a logout clears them all" |
| 4 | No app heartbeat; at most 20 rooms; leave rooms | Not a reducer rule. `socket.test.ts`: "sends no ping of its own", "refuses a 21st room; leaving a room unsubscribes" |
| 5 | REST fallback; four connection states; stale never looks connected | `socket.test.ts`: "Offline at once on the offline event, reconnecting when back", "Reconnecting on close, and after 12 s of silence in a subscribed room", "presence frames keep a quiet room connected", "a reconnect ... before Reconnected". `roomController.test.ts`: "bids go over the socket when it is open and over REST while it is down". `room.test.ts`: "a room is stale unless connected and subscribed" |
| 6 | Buffer until subscribed, then apply above snapshot.sequence_no | `room.test.ts`: "buffers events until subscribed, then applies only those above snapshot.sequence_no" |
| 7 | Sequence order, drop repeats, (type, seq) dedupe, final, 300 ms gap | `room.test.ts`: "applies events in sequence order and drops repeats", "timer_extended sharing its bid sequence is applied once", "no further extension from final or extension_count 10 on either event", "a gap waits 300 ms, then refetches" |
| 8 | Presence ahead, or degraded false, refetches | `room.test.ts`: "presence ahead of the last sequence refetches", "system_status degraded false refetches" |
| 9 | auction_closed always applied | `room.test.ts`: "auction_closed is applied whatever its sequence", "auction_closed first applies buffered bids up to final_sequence_no, refetching on a gap", "a terminal /live status closes the room without auction_closed" |
| 10, B1 | Terminal status is sticky | `room.test.ts`: "terminal status is sticky: later presence, snapshots or bids never reopen bidding" |
| 11 | Scheduled goes live only on the server's word | `room.test.ts`: "auction_started takes a scheduled room live and refetches", "a /live snapshot saying live takes it live", "the local clock passing start_time never enables bidding" |
| B1 | An event while scheduled triggers a refetch | `room.test.ts`: "a bid or presence frame in a scheduled room refetches /live" |
| B1 | Pollers keep the higher sequence and the later status | `room.test.ts`: "a polled snapshot with a lower sequence_no or an earlier status is ignored" |
| 12 | Countdown from end_time_ms and the server clock, fastest round trip | Not a reducer rule. `clock.test.ts`: "keeps the offset from the fastest round trip", "a frame's server time raises a too-low offset but never lowers one", "remaining time comes from end_time_ms and the offset, never below zero". `client.test.ts`: "a stamped body feeds the clock with sent and received times" |
| 13 | request_id; pending; reconcile on ack; rejection with min_next | `room.test.ts`: "a sent bid is pending until its ack, then takes its sequence", "a rejection shows its code and min_next and clears the pending bid". `client.test.ts`: "request_id is 1 to 64 of [A-Za-z0-9_-], checked before any request". `contract.test.ts`: "a fresh id is 1 to 64 of [A-Za-z0-9_-]" |
| 14 | Retry SERVICE_UNAVAILABLE with the same request_id | `client.test.ts`: "SERVICE_UNAVAILABLE and network failures retry with the same request_id, then succeed", "the retry is bounded: six attempts, then the error surfaces". `room.test.ts`: "a socket SERVICE_UNAVAILABLE keeps the bid pending and retries with the same request_id" |
| 15, B1 | Own status from acks, outbid_notice, you_lead and your_result; "You" labels may use alias equality | `room.test.ts`: "leading comes from own acks, outbid_notice and you_lead only", "you_lead and your_result from a fresh snapshot set leading, won and lost", "an alias match labels a row You but never makes the viewer leading, outbid, won or lost", "own rows are marked by own ack sequence numbers" |
| 16 | Submit disabled while pending; respect RATE_LIMITED | `room.test.ts`: "submit is disabled while a bid is pending", "RATE_LIMITED disables submit until rateLimitOver" |
| 17 | No controls on the viewer's own auction | `room.test.ts`: "the seller own auction offers no bid or proxy control" |
| 18 | reserve_met is tri-state; never an amount | `room.test.ts`: "reserve_met null, true and false map to none, met and not met". `contract.test.ts`: "no room type can carry a reserve amount or another bidder maximum" |
| 19 | A maximum only in its owner's view; never logged | `room.test.ts`: "your_max lives only in the viewer part, never in the public view". `lint.test.ts`: "console is banned (item 19)" |
| 20 | Integer paise; never parseFloat or toFixed | Not a reducer rule. `money.test.ts` (the brief 11.3 lines, "formatting refuses anything that is not safe non-negative paise", "parseRupees rejects precision, sign, junk and unsafe amounts"). `lint.test.ts`: "parseFloat is banned", "toFixed is banned" |
| 21 | UTC on the wire, viewer zone on screen | Not a reducer rule. `clock.test.ts`: "formats in the viewer zone, 24-hour", "a datetime-local value becomes UTC ISO and round-trips" |
| 22 | Token in memory only; refresh on load with X-Requested-With | Not a reducer rule. `auth.test.ts`: "sends X-Requested-With and same-origin credentials, keeps the token in memory", "204 leaves the viewer anonymous", every auth test fails on any touch of localStorage, sessionStorage or IndexedDB. `e2e/shell.spec.ts`: the refresh carries the header and gets 204 |
| 23 | Strip the token from /verify-email and /reset-password | Not in this slice: tested with those screens in a later 10c slice. The Referrer-Policy header is checked in `e2e/shell.spec.ts` |
| 24 | User text as text; CSP | Not a reducer rule. `lint.test.ts`: "dangerouslySetInnerHTML is banned (item 24)". `e2e/shell.spec.ts`: the exact CSP header, zero violations |
| 25 | Razorpay on its own page and CSP | Not in this slice: the payment slice |
| 26 | Refetch the unread count after a reconnect | Not a reducer rule. `socket.test.ts`: "a reconnect ... resyncs" (the resync hook); the notifications slice uses that hook |
| 27 | Degraded disables bidding | `room.test.ts`: "degraded (system_status or /live) disables bidding and marks the room stale" |
| 28 | Parse frames defensively | Not a reducer rule. `contract.test.ts`: "unknown frame types are ignored", "frames missing or mistyping a required field are dropped", "invalid JSON and non-objects are dropped, never thrown". `socket.test.ts`: "malformed frames never reach the room". `roomController.test.ts`: "a malformed frame never reaches the reducer" |
| 29 | One error envelope; catalogue message; fields onto inputs | Not a reducer rule. `client.test.ts`: "a non-2xx body becomes ApiError with code, message and fields". `strings.test.ts`: "every documented error code has a message, and no undocumented one" |
| B1 | self_outbid_blocked: the leader bids again only when the policy is off | `room.test.ts`: "the leader may bid again only when the policy is off" |

## Budgets (brief 11.5)

`npm run build` ends with `scripts/chunk-budget.mjs`: it prints every JavaScript chunk's gzip size and fails when the entry chunk plus its static imports exceed 130 KB gzip. Routes beyond the shell load through dynamic imports, and `src/app/chunks.test.ts` fails if the entry ever imports `live/` or `room/` code statically. `e2e/budgets.spec.ts` sums every font file fetched before the load event on each route, at 390 and 1440, against 120 KB.

## Project lint rules

| Rule | Brief |
|---|---|
| `no-console` | 13 item 19: a maximum never reaches the console |
| `dangerouslySetInnerHTML` banned | 13 item 24: user text is rendered as text |
| No string literal in JSX: text between tags, a string or template in braces, or `alt`, `title`, `placeholder`, `aria-label`, `aria-description`, `aria-valuetext` | OR-11: strings come from `useStrings()` |
| `parseFloat` and `toFixed` banned | 13 item 20: money is integer paise; `lib/money.ts` formats and parses |

`src/lint.test.ts` runs ESLint on small samples and proves each rule fires.
