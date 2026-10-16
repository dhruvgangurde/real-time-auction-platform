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
  };
  home: {
    title: string;
    lede: string;
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
    admin: {
      heading: string;
      note: string;
      forceClose: string;
      cancel: string;
      forceTitle: string;
      forceBody: string;
      current: (amount: string, seq: number, reserve: string) => string;
      reserveMet: string;
      reserveNotMet: string;
      noBidsYet: string;
      cannotUndo: string;
      cancelTitle: string;
      cancelBody: string;
      reasonLabel: string;
      reasonNote: string;
      keepOpen: string;
      failed: string;
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
  placeholder: {
    title: string;
    body: string;
  };
  help: {
    title: string;
    lede: string;
    apiDocs: string;
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
  belowMinimum: string;
  statusTitle: string;
  statusWon: (name: string, amount: string) => string;
  statusAwaiting: string;
  signInTitle: string;
  signInNote: string;
  signIn: string;
  testMode: string;
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
