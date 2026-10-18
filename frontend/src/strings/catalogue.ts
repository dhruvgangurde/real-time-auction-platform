// The one typed string catalogue (OR-11). Components never hold a string literal: they take the
// active catalogue from context (useStrings) and render its fields; functions interpolate. A
// second locale is one more file that satisfies Catalogue. Every error code of architecture.md
// section 9 and every close code of section 10 must have a message, so a missing one fails tsc.
import { createContext, createElement, useContext, type ReactNode } from 'react';

import type { SectionKey } from '../sell/dashboard';
import type { Problem } from '../sell/listing';

export type ErrorCode =
  | 'ACCOUNT_SUSPENDED'
  | 'ALREADY_CLOSED'
  | 'ALREADY_PAID'
  | 'AMOUNT_BELOW_GATEWAY_MINIMUM'
  | 'AUCTION_ALREADY_LIVE'
  | 'AUCTION_CLOSED'
  | 'AUCTION_NOT_LIVE'
  | 'AUCTION_NOT_STARTED'
  | 'BELOW_CURRENT_PRICE'
  | 'BID_ABOVE_LIMIT'
  | 'BID_TOO_LOW'
  | 'CANNOT_TARGET_SELF'
  | 'CATEGORY_CYCLE'
  | 'CATEGORY_IN_USE'
  | 'CATEGORY_NOT_FOUND'
  | 'DEMO_COOLING_DOWN'
  | 'DEMO_NOT_PAYABLE'
  | 'DEMO_UNAVAILABLE'
  | 'EMAIL_NOT_VERIFIED'
  | 'EMAIL_TAKEN'
  | 'FORBIDDEN'
  | 'IMAGE_TOO_LARGE'
  | 'INTERNAL_ERROR'
  | 'INVALID_CREDENTIALS'
  | 'INVALID_SIGNATURE'
  | 'INVALID_TOKEN'
  | 'LAST_ADMIN'
  | 'MESSAGE_TOO_LARGE'
  | 'NOT_DRAFT'
  | 'NOT_FOUND'
  | 'NOT_SUBSCRIBED'
  | 'NOT_SUSPENDED'
  | 'NOT_WATCHING'
  | 'NOT_WINNER'
  | 'PAYLOAD_TOO_LARGE'
  | 'PAYMENT_MISMATCH'
  | 'PROXY_CANNOT_DECREASE'
  | 'RATE_LIMITED'
  | 'SELF_OUTBID_NOT_ALLOWED'
  | 'SELLER_CANNOT_BID'
  | 'SERVICE_UNAVAILABLE'
  | 'SLUG_TAKEN'
  | 'TOKEN_EXPIRED'
  | 'TOO_MANY_IMAGES'
  | 'TOO_MANY_SUBSCRIPTIONS'
  | 'UNKNOWN_TYPE'
  | 'UNSUPPORTED_IMAGE'
  | 'VALIDATION_ERROR';

export type CloseCode =
  | 1000
  | 1001
  | 1008
  | 1009
  | 1011
  | 4001
  | 4002
  | 4003
  | 4004
  | 4008
  | 4009
  | 4010
  | 4012;

export type ConnectionState = 'offline' | 'reconnecting' | 'connected' | 'reconnected';
export type HelpTopic = 'reserve' | 'increment' | 'maximum' | 'clock';
export const HELP_TOPICS: readonly HelpTopic[] = ['reserve', 'increment', 'maximum', 'clock'];

export type Catalogue = {
  locale: string;
  app: {
    name: string;
    skipToContent: string;
    main: string;
    mainNav: string;
  };
  nav: {
    live: string;
    how: string;
    signIn: string;
    start: string;
    watchlist: string;
    notifications: string;
    unread: (n: number) => string;
    /** the bell's accessible name: "Notifications" or "Notifications, 3 unread" */
    notificationsLabel: (n: number) => string;
    account: string;
    sell: string;
    admin: string;
  };
  /** the landing's h1 (brief 8.1), in the entry so it paints with the shell; the rest of the
   * landing's strings are in strings/landing-en-IN.ts, imported by the landing chunk only */
  home: {
    lines: readonly [string, string, string];
  };
  notFound: {
    title: string;
    body: string;
    back: string;
  };
  connection: Record<ConnectionState, string> & {
    polling: string;
    otherTab: string;
    useThisTab: string;
    stale: (time: string) => string;
  };
  room: {
    participants: (n: number) => string;
    minNext: (amount: string) => string;
    pending: string;
    accepted: (amount: string) => string;
    outbid: (amount: string, minNext: string) => string;
    reserveMet: string;
    reserveNotMet: string;
  };
  /** the room's regions (brief 9.3; copy from design/mockups/room.html) */
  roomUi: {
    rupee: string;
    labels: { live: string; lot: string; crumbs: string; help: string };
    units: { minutes: (n: number) => string; seconds: (n: number) => string; minute: string };
    status: {
      connecting: string;
      connected: (n: number) => string;
      polling: (n: number) => string;
      otherTab: string;
      useThisTab: string;
      reconnecting: string;
      lastUpdate: (time: string) => string;
      opens: (time: string, n: number) => string;
      draft: string;
    };
    price: {
      current: string;
      starting: string;
      outbid: string;
      wonAt: string;
      soldFor: string;
      highest: string;
      lastBid: string;
      leads: (alias: string, seq: number, byMaximum: boolean) => string;
      youLead: (seq: number, byMaximum: boolean) => string;
      nextAtLeast: (amount: string) => string;
      noBids: (start: string) => string;
      opens: (time: string) => string;
      outbidLine: (amount: string, minNext: string) => string;
      maxReachedLine: (max: string, amount: string, minNext: string) => string;
      wonLine: (seq: number, time: string, extensions: number) => string;
      lostLine: (seq: number, time: string, extensions: number, yourHighest: string | null) => string;
      noSaleLine: (time: string, seq: number) => string;
      cancelledLine: (seq: number) => string;
      waiting: string;
      youLeadWaiting: (seq: number) => string;
      reserveMet: string;
      reserveNotMet: string;
      hasReserve: string;
    };
    clock: {
      ends: (time: string) => string;
      endsLabel: string;
      timeStop: (time: string) => string;
      lastMinute: (window: string, extension: string) => string;
      extendedWhy: (extension: string, window: string, n: number) => string;
      extendedShort: (n: number) => string;
      cap: (time: string) => string;
      closing: string;
      mayExtended: string;
      untilOpens: (opens: string, ends: string) => string;
      rulerLabel: (ends: string, window: string) => string;
      now: string;
      rulerEnds: (time: string) => string;
      rulerWas: (time: string) => string;
      keyLastMinute: (window: string, extension: string) => string;
      keyAdded: (extension: string, seq: number) => string;
    };
    ledger: {
      title: string;
      history: string;
      no: string;
      bidder: string;
      amount: string;
      decidedAt: string;
      you: string;
      pendingSeq: string;
      byMaximum: string;
      sending: string;
      checking: string;
      none: string;
    };
    lot: {
      listing: string;
      demo: string;
      category: string;
      seller: string;
      bidding: string;
      startingBid: string;
      softClose: string;
      reserve: string;
      reserveYes: string;
      reserveOwn: (amount: string) => string;
      reserveNo: string;
      window: (start: string, end: string) => string;
      risingBy: (start: string, increment: string) => string;
      softCloseText: (window: string, extension: string) => string;
      photoAlt: (title: string) => string;
    };
    banner: {
      open: string;
      openDetail: (start: string, end: string) => string;
      degraded: string;
      degradedDetail: string;
      reconnecting: string;
      reconnectingDetail: string;
      signedOut: string;
      signedOutDetail: string;
    };
    bid: {
      heading: string;
      anonHeading: string;
      fieldLabel: string;
      minHint: (amount: string) => string;
      theMinimum: (amount: string) => string;
      bid: (amount: string) => string;
      sending: (amount: string) => string;
      checking: (amount: string) => string;
      opensAt: (time: string) => string;
      youLead: string;
      youLeadNote: string;
      paused: string;
      pausedNote: string;
      ended: string;
      signInToBid: string;
      registerPrompt: string;
      register: string;
      pay: (amount: string) => string;
      paidOn: (time: string) => string;
      receipt: string;
      demoSale: string;
      pendingNote: string;
      retryingHead: string;
      retryingNote: string;
      acceptedHead: (seq: number, amount: string) => string;
      rejectedMin: (amount: string) => string;
      rateLimitedNote: (amount: string) => string;
      over3x: (amount: string, current: string) => string;
      over3xNote: (limit: string) => string;
      confirm: (amount: string) => string;
      changeAmount: string;
      signedOutHead: string;
      signedOutNote: string;
      verifyHead: string;
      verifyNote: string;
      resend: string;
      suspendedHead: string;
      suspendedNote: string;
      invalid: string;
      aboveLimit: (limit: string) => string;
    };
    proxy: {
      set: string;
      setAction: string;
      raise: string;
      yours: string;
      reached: string;
      setNew: string;
      only: string;
      intro: string;
      activeNote: (amount: string) => string;
      reachedNote: string;
      scheduledNote: (time: string) => string;
      fieldLabel: string;
      placeholder: (amount: string) => string;
      confirm: string;
    };
    seller: {
      heading: string;
      reserveBadge: string;
      reserveValue: (amount: string) => string;
      met: string;
      notMet: string;
      cantBid: string;
      biddersSee: string;
    };
  };
  /** the live-region text (brief 11.4), exactly as design/shots/03-room/index.md lists it */
  announce: {
    price: (amount: string, leader: string | null, youLead: boolean, reserveMet: boolean | null) => string;
    open: (startPrice: string, endTime: string) => string;
    notPlaced: (reason: string) => string;
    minimumNow: (amount: string) => string;
    extended: (span: string, window: string, n: number, endTime: string) => string;
    capReached: (span: string, endTime: string) => string;
    closing: (endTime: string) => string;
    won: (amount: string) => string;
    sold: (amount: string) => string;
    noSale: string;
    cancelled: string;
    degraded: string;
    reconnecting: string;
    signedOut: string;
    maxSet: (amount: string, leading: boolean) => string;
    maxReached: (amount: string, outbidAt: string, minNext: string) => string;
    /** countdown milestones at 5 min, 1 min, 30 s and 10 s (brief 11.4) */
    minutesLeft: (n: number) => string;
    secondsLeft: (n: number) => string;
  };
  common: {
    loading: string;
    required: string;
    back: string;
    tryAgain: string;
    trying: string;
  };
  /** a listing card, on Discover and the watchlist (brief 9.2, 9.7) */
  cards: {
    status: Record<'draft' | 'scheduled' | 'live' | 'closing' | 'sold' | 'noSale' | 'cancelled', string>;
    startingBid: string;
    currentBid: string;
    soldFor: string;
    startsAt: (time: string) => string;
    endsIn: (span: string) => string;
    endsOn: (time: string) => string;
    closingNote: string;
    noSaleNote: string;
    cancelledNote: string;
    bids: (n: number) => string;
  };
  /** the Watch toggle (FR-24, FR-73) */
  watch: {
    watch: string;
    watching: string;
    again: string;
    note: string;
    cancelled: string;
    addFailed: string;
    removeFailed: string;
  };
  notifications: {
    /** the polite line when the unread count rises (brief 9.6) */
    arrived: (n: number) => string;
  };
  /** the notifications page (brief 9.6, FR-74); one label and sentence per type (FR-70) */
  notificationsPage: {
    title: string;
    filterLabel: string;
    all: string;
    unread: string;
    markAll: string;
    markRead: string;
    markReadLabel: (text: string) => string;
    unreadMark: string;
    empty: string;
    emptyNote: string;
    caughtUp: string;
    showAll: string;
    error: string;
    failed: string;
    showMore: string;
    loadingMore: string;
    labels: Record<
      | 'outbid'
      | 'ending_soon'
      | 'auction_won'
      | 'auction_lost'
      | 'payment_due'
      | 'payment_received'
      | 'payment_failed'
      | 'auction_sold'
      | 'auction_no_sale'
      | 'other',
      string
    >;
    text: {
      outbid: (title: string, price: string) => string;
      endingSoon: (title: string, time: string) => string;
      won: (title: string, price: string) => string;
      lostSold: (title: string, price: string) => string;
      lostNoSale: (title: string) => string;
      paymentDue: (price: string, title: string) => string;
      paymentReceived: (price: string, title: string) => string;
      paymentFailed: (price: string, title: string) => string;
      sold: (title: string, price: string) => string;
      noSale: (title: string) => string;
      noSaleReserve: (title: string) => string;
      other: (title: string) => string;
      otherNoTitle: string;
    };
  };
  /** email choices per notification type (FR-72, FR-74) */
  emailPrefs: {
    title: string;
    lede: string;
    labels: Record<
      'outbid' | 'ending_soon' | 'auction_won' | 'auction_lost' | 'payment_due' | 'payment_received' | 'payment_failed' | 'auction_sold' | 'auction_no_sale',
      string
    >;
    save: string;
    saving: string;
    saved: string;
    failed: string;
    error: string;
  };
  watchlist: {
    title: string;
    lede: string;
    empty: string;
    find: string;
    error: string;
  };
  discover: {
    title: string;
    searchLabel: string;
    searchPlaceholder: string;
    search: string;
    filters: string;
    filtersToggle: (n: number) => string;
    category: string;
    anyCategory: string;
    status: string;
    statusNames: Record<'scheduled' | 'live' | 'closing' | 'closed_sold' | 'closed_no_sale', string>;
    statusHint: string;
    price: string;
    priceHint: string;
    min: string;
    max: string;
    closingBefore: string;
    apply: string;
    clear: string;
    wholeRupees: string;
    range: string;
    filteredBy: string;
    active: {
      search: (q: string) => string;
      category: (name: string) => string;
      status: (names: string) => string;
      price: (range: string) => string;
      from: (amount: string) => string;
      upTo: (amount: string) => string;
      between: (min: string, max: string) => string;
      closing: (time: string) => string;
    };
    empty: string;
    upcoming: string;
    noMatch: string;
    unknownCategory: string;
    error: string;
    rateLimited: (seconds: number) => string;
    showMore: string;
    loadingMore: string;
    results: string;
  };
  history: {
    title: string;
    toRoom: string;
    no: string;
    bidder: string;
    amount: string;
    decidedAt: string;
    you: string;
    byMaximum: string;
    showMore: string;
    loading: string;
    empty: string;
    degraded: string;
    error: string;
  };
  guards: {
    deniedTitle: string;
    denied: (role: 'seller' | 'admin') => string;
    unreachableTitle: string;
    unreachable: string;
  };
  help: {
    title: string;
    lede: string;
    close: string;
    openLabel: (title: string) => string;
    topics: Record<
      HelpTopic,
      {
        title: string;
        question: string;
        body: string[];
        example: string;
        diagram: string;
        labels: string[];
      }
    >;
  };
  auth: {
    register: {
      title: string;
      name: string;
      email: string;
      password: string;
      passwordHint: string;
      submit: string;
      haveAccount: string;
      signIn: string;
    };
    checkEmail: {
      title: string;
      body: (email: string) => string;
      bodyNoEmail: string;
      resend: string;
      resent: string;
    };
    verify: {
      title: string;
      verifying: string;
      done: string;
      signIn: string;
      incomplete: string;
      resendTitle: string;
    };
    signIn: {
      title: string;
      email: string;
      password: string;
      submit: string;
      forgot: string;
      noAccount: string;
      register: string;
      notVerifiedResend: string;
      rateLimited: (seconds: number) => string;
    };
    forgot: {
      title: string;
      body: string;
      email: string;
      submit: string;
      done: string;
    };
    reset: {
      title: string;
      password: string;
      submit: string;
      done: string;
      signIn: string;
      incomplete: string;
    };
    signOut: {
      label: string;
      failed: string;
    };
  };
  errors: Record<ErrorCode, string>;
  close: Record<CloseCode, string>;
  unknownError: string;
};

/** The payment page's strings (brief 9.5). A catalogue of their own, imported only by the payment
 * document (strings/pay-en-IN.ts), so the SPA's entry chunk does not carry them; error codes and
 * the shared lines still come from Catalogue. */
export type PayCatalogue = {
  title: string;
  back: string;
  lotLabel: string;
  wonFor: (amount: string) => string;
  pay: (amount: string) => string;
  opening: string;
  open: string;
  confirmingTitle: string;
  confirming: string;
  confirmingNote: string;
  slow: string;
  unverified: string;
  paidTitle: string;
  paid: (amount: string) => string;
  paidOn: (time: string) => string;
  paymentId: (id: string) => string;
  failedTitle: string;
  failed: string;
  tryAgain: string;
  lastFailed: string;
  dismissed: string;
  scriptFailed: string;
  unavailable: string;
  rateLimited: (seconds: number) => string;
  notWinner: string;
  notFound: string;
  noSale: string;
  demo: string;
  belowMinimum: string;
  statusTitle: string;
  statusWon: (name: string, amount: string) => string;
  statusAwaiting: string;
  signInTitle: string;
  signInNote: string;
  signIn: string;
  testMode: string;
};

/** The account pages' strings (brief 9.10; FR-9, FR-10), imported only by the account chunk
 * (strings/account-en-IN.ts). Error codes still come from Catalogue. */
export type AccountCatalogue = {
  title: string;
  profile: {
    title: string;
    name: string;
    nameHint: string;
    save: string;
    saving: string;
    saved: string;
    email: string;
    emailNote: string;
    status: string;
    roles: string;
    statuses: Record<'pending_verification' | 'active' | 'suspended', string>;
    roleNames: Record<'bidder' | 'seller' | 'admin', string>;
    problems: Record<'required' | 'long', string>;
  };
  links: { title: string } & Record<'bids' | 'listings' | 'admin' | 'email', string>;
  password: {
    title: string;
    note: string;
    send: string;
    sending: string;
    sent: (email: string) => string;
  };
  signOut: { title: string; note: string };
  bids: {
    title: string;
    lede: string;
    auction: string;
    bid: (n: number, amount: string) => string;
    byMaximum: string;
    on: (time: string) => string;
    empty: string;
    find: string;
    more: string;
    back: string;
  };
};

/** The administrator's strings (brief 9.9, UI-8), imported only by the admin chunk (strings/
 * admin-en-IN.ts): a non-admin never downloads them. Error codes still come from Catalogue. */
export type AdminCatalogue = {
  area: {
    title: string;
    nav: string;
    users: string;
    audit: string;
    categories: string;
  };
  users: {
    title: string;
    q: string;
    role: string;
    anyRole: string;
    status: string;
    anyStatus: string;
    search: string;
    roles: Record<'bidder' | 'seller' | 'admin', string>;
    statuses: Record<'pending_verification' | 'active' | 'suspended', string>;
    you: string;
    joined: (day: string) => string;
    empty: string;
    previous: string;
    next: string;
    page: (n: number) => string;
  };
  people: {
    roleNoun: Record<'seller' | 'admin', string>;
    suspend: string;
    reinstate: string;
    grant: (role: string) => string;
    revoke: (role: string) => string;
    actionFor: (action: string, name: string) => string;
    suspendTitle: (name: string, email: string) => string;
    suspendBody: string;
    reinstateTitle: (name: string, email: string) => string;
    reinstateBody: string;
    grantTitle: (role: string, name: string, email: string) => string;
    grantBody: string;
    revokeTitle: (role: string, name: string, email: string) => string;
    revokeBody: string;
    keep: string;
    suspended: (name: string) => string;
    reinstated: (name: string) => string;
    granted: (role: string, name: string) => string;
    revoked: (role: string, name: string) => string;
  };
  access: {
    title: string;
    body: string;
    retry: string;
    checking: string;
  };
  categories: {
    title: string;
    newTitle: string;
    name: string;
    slug: string;
    slugHint: string;
    parent: string;
    top: string;
    create: string;
    edit: string;
    editLabel: (name: string) => string;
    editTitle: (name: string) => string;
    save: string;
    cancel: string;
    remove: string;
    removeLabel: (name: string) => string;
    removeTitle: (name: string) => string;
    removeBody: string;
    keep: string;
    created: (name: string) => string;
    saved: (name: string) => string;
    removed: (name: string) => string;
    unchanged: string;
    empty: string;
    problems: Record<'required' | 'long' | 'slug', string>;
  };
  audit: {
    title: string;
    actor: string;
    type: string;
    anyType: string;
    types: Record<'auction' | 'user' | 'category' | 'settlement' | 'payment' | 'bid', string>;
    kinds: Record<'auction' | 'user' | 'category' | 'settlement' | 'payment' | 'bid', string>;
    byActorLabel: (actor: string) => string;
    id: string;
    from: string;
    to: string;
    filter: string;
    system: string;
    by: (actor: string) => string;
    reason: (reason: string) => string;
    entity: (type: string, id: string) => string;
    field: string;
    before: string;
    after: string;
    hidden: string;
    none: string;
    yes: string;
    no: string;
    empty: string;
    more: string;
  };
  room: {
    heading: string;
    note: string;
    forceClose: string;
    cancel: string;
    forceTitle: (title: string) => string;
    forceBody: string;
    current: (amount: string, seq: number, reserve: string) => string;
    reserveMet: string;
    reserveNotMet: string;
    noBidsYet: string;
    cannotUndo: string;
    cancelTitle: (title: string) => string;
    cancelBody: string;
    reasonLabel: string;
    reasonNote: string;
    keepOpen: string;
  };
};

/** The landing's strings (brief 8), imported only by the landing chunk (strings/landing-en-IN.ts),
 * so the entry does not carry them. Amounts and times arrive formatted. */
export type LandingCatalogue = {
  hero: {
    lede: string;
    ledeReplay: string;
    ledeEmpty: string;
    ledeRunning: string;
    watch: string;
    start: string;
    browse: string;
    actions: string;
  };
  board: {
    label: string;
    demoLot: string;
    replayOf: (day: string, time: string) => string;
    replayNote: (day: string, at: string, total: string) => string;
    loading: string;
    currentBid: string;
    startingPrice: string;
    leads: (alias: string, sequence: number) => string;
    bids: (n: number) => string;
    noBids: (amount: string) => string;
    ends: (time: string, windowS: number, extension: string) => string;
    extended: (n: number) => string;
    closedAt: (time: string) => string;
    sold: (alias: string, amount: string, sequence: number) => string;
    noSale: string;
    noReserve: string;
    reserveMet: string;
    reserveNotMet: string;
    ledger: { caption: string; sequence: string; bidder: string; amount: string; decided: string; byMaximum: string };
    earlier: (n: number) => string;
    again: string;
    emptyTitle: string;
    emptyRunning: string;
    emptyBody: string;
  };
  break: {
    title: string;
    intro: string;
    send: string;
    sending: string;
    runningLeft: (left: string) => string;
    settlingButton: string;
    nextRun: (left: string) => string;
    joined: string;
    willCheck: string;
    will: readonly string[];
    unavailable: {
      off: string;
      disabled: string;
      runner_offline: string;
      daily_cap: string;
      rate_limited: string;
      rateLimitedUntil: (time: string) => string;
      unreachable: string;
    };
    running: (ends: string) => string;
    counters: { connected: string; of: (n: number, m: number) => string; sent: string; accepted: string; rejected: string; price: string; left: string };
    watchingRow: string;
    watchingState: string;
    settling: string;
    lastRun: (ended: string, sent: number, accepted: number, rejected: number) => string;
    rejectedBy: (line: string) => string;
    postgres: string;
    watcherHead: string;
    watcherLine: (received: number, expected: number) => string;
    watcherPending: string;
    watcherFail: string;
    pass: string;
    fail: string;
    failMeaning: Record<string, string>;
    ledger: string;
    how: string;
    expected: (what: string) => string;
    stopped: string;
    stoppedReason: (reason: string) => string;
    announce: Record<'none' | 'running' | 'settling' | 'verified' | 'failed' | 'stopped', string>;
    reasons: Record<string, string>;
    values: {
      gapless: (lowest: number, highest: number, n: number) => string;
      missing: (list: string) => string;
      distinct: (n: number, distinct: number) => string;
      winner: (alias: string, amount: string, sequence: number) => string;
      winnerFail: (alias: string, amount: string, expected: string, expectedAmount: string) => string;
      settlements: (n: number) => string;
      closes: (n: number) => string;
      acked: (acknowledged: number, missing: number) => string;
      none: string;
    };
  };
  measured: {
    title: string;
    labels: Record<'NFR-1' | 'NFR-2' | 'NFR-3' | 'NFR-4' | 'NFR-5' | 'NFR-28', string>;
    targets: Record<'NFR-1' | 'NFR-2' | 'NFR-3' | 'NFR-4' | 'NFR-5' | 'NFR-28', string>;
    details: { 'NFR-1': (n: string) => string; 'NFR-28': (n: string) => string };
    when: (date: string, k6: string) => string;
    machine: (cpu: string, threads: number, ramGib: number, dockerCpus: number, dockerGib: number) => string;
    results: string;
    scripts: string;
  };
  featured: {
    title: string;
    none: string;
    failed: string;
    browse: string;
  };
  built: {
    title: string;
    drawing: {
      browser: string;
      browserSub: string;
      api1: string;
      api2: string;
      apiSub: string;
      api1Short: string;
      api2Short: string;
      apiSubShort: readonly string[];
      razorpay: string;
      razorpaySub: readonly string[];
      razorpayShort: string;
      redis: string;
      redisSub: readonly string[];
      redisSubShort: readonly string[];
      worker: string;
      workerSub: readonly string[];
      postgres: string;
      postgresSub: readonly string[];
      postgresShort: string;
      oneAtATime: string;
      outbox: string;
      writes: string;
      reads: string;
    };
    equivalent: readonly string[];
    decisions: readonly (readonly [string, string])[];
    architecture: string;
    decisionsLink: string;
    footer: { testMode: string; demo: string; help: string; source: string };
  };
};

/** The seller screens' strings (brief 9.8), imported only by the seller chunk (strings/
 * seller-en-IN.ts), so the entry does not carry them; error codes still come from Catalogue. */
export type SellerCatalogue = {
  problems: Record<Problem, string>;
  dashboard: {
    title: string;
    lede: string;
    list: string;
    empty: string;
    more: string;
    sections: Record<SectionKey, string>;
  };
  form: {
    newTitle: string;
    newLede: string;
    title: string;
    description: string;
    descriptionHint: string;
    category: string;
    categoryChoose: string;
    startingPrice: string;
    startingHint: string;
    minIncrement: string;
    incrementHint: string;
    reserve: string;
    reserveHint: string;
    schedule: string;
    start: string;
    end: string;
    zone: (zone: string) => string;
    softClose: string;
    threshold: string;
    extension: string;
    softCloseHint: string;
    create: string;
    saving: string;
    leaveTitle: string;
    leaveBody: string;
    leave: string;
    stay: string;
  };
  page: {
    back: string;
    room: string;
    draftNote: string;
    descriptive: string;
    locked: string;
    ended: string;
    save: string;
    saved: string;
    unchanged: string;
    notYours: string;
  };
  photos: {
    title: string;
    cover: string;
    none: string;
    coverTag: string;
    alt: (n: number, total: number) => string;
    preview: (name: string) => string;
    remove: string;
    removeLabel: (n: number, total: number) => string;
    removeTitle: (n: number, total: number) => string;
    removeBody: string;
    keep: string;
    keepOne: string;
    add: string;
    addHint: string;
    waiting: (name: string) => string;
    uploading: (name: string) => string;
    failed: (name: string, why: string) => string;
    dismiss: string;
    dismissLabel: (name: string) => string;
  };
  actions: {
    publish: string;
    publishTitle: (title: string) => string;
    publishBody: (start: string, end: string, zone: string) => string;
    publishNote: string;
    notYet: string;
    published: string;
    cancel: string;
    cancelTitle: (title: string) => string;
    cancelBody: string;
    keep: string;
    cancelled: string;
  };
  closed: {
    title: string;
    notSold: string;
    reserveNotMet: string;
  };
};

/**
 * A sentence never renders with a value missing: every catalogue function returns '' when any
 * argument is '' or NaN (an amount or time that is not known yet), so the UI shows nothing rather
 * than "The first bid can be ." Wrapped functions keep their arity.
 */
export function guardSentences<T>(catalogue: T): T {
  const walk = (value: unknown): unknown => {
    if (typeof value === 'function') {
      const sentence = value as (...args: unknown[]) => unknown;
      const guarded = (...args: unknown[]) => (args.some((a) => a === '' || Number.isNaN(a)) ? '' : sentence(...args));
      return Object.defineProperty(guarded, 'length', { value: sentence.length });
    }
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      return Object.fromEntries(Object.entries(value).map(([key, inner]) => [key, walk(inner)]));
    }
    return value;
  };
  return walk(catalogue) as T;
}

const StringsContext = createContext<Catalogue | null>(null);

export function StringsProvider(props: { catalogue: Catalogue; children: ReactNode }) {
  return createElement(StringsContext.Provider, { value: props.catalogue }, props.children);
}

export function useStrings(): Catalogue {
  const catalogue = useContext(StringsContext);
  if (catalogue === null) {
    throw new Error('useStrings needs a StringsProvider');
  }
  return catalogue;
}

/** The message for an error code; unknown codes (new backend, old client) get a plain line. */
export function errorMessage(catalogue: Catalogue, code: string): string {
  return (catalogue.errors as Record<string, string | undefined>)[code] ?? catalogue.unknownError;
}
