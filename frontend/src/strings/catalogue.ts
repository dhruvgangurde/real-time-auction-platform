// The one typed string catalogue (OR-11). Components never hold a string literal: they take the
// active catalogue from context (useStrings) and render its fields; functions interpolate. A
// second locale is one more file that satisfies Catalogue. Every error code of architecture.md
// section 9 and every close code of section 10 must have a message, so a missing one fails tsc.
import { createContext, createElement, useContext, type ReactNode } from 'react';

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
    account: string;
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
  common: {
    loading: string;
    required: string;
    back: string;
  };
  guards: {
    deniedTitle: string;
    denied: (role: 'seller' | 'admin') => string;
  };
  placeholder: {
    title: string;
    body: string;
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
