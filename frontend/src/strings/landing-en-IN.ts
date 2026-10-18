// The landing's strings, English (India) (brief 8). Amounts and times arrive formatted.
import { guardSentences, type LandingCatalogue } from './catalogue';

export const landingEnIN = guardSentences({
  hero: {
    lede: 'A real auction is running below. One atomic step decides every bid, so the ledger never skips a number and never counts one twice.',
    ledeReplay:
      'A recorded demo auction replays below. One atomic step decided every bid, so the ledger never skips a number and never counts one twice.',
    ledeEmpty:
      'When the demo runs, a real auction appears below. One atomic step decides every bid, so the ledger never skips a number and never counts one twice.',
    watch: 'Watch a live auction',
    start: 'Start bidding',
    browse: 'Browse auctions',
    actions: 'Get started',
  },
  board: {
    label: 'Demo auction',
    demoLot: 'Demo lot',
    replayOf: (day, time) => `Replay of a real run, ${day}, ${time}`,
    replayNote: (day, at, total) => `Replaying the run of ${day} at its recorded pace: ${at} of ${total}.`,
    loading: 'Loading the recorded run.',
    currentBid: 'Current bid',
    startingPrice: 'Starting price',
    leads: (alias, sequence) => `${alias} leads with bid №${sequence}.`,
    bids: (n) => (n === 1 ? '1 bid.' : `${n} bids.`),
    noBids: (amount) => `No bids yet. The first bid can be ${amount}.`,
    ends: (time, windowS, extension) => `Ends ${time}. A bid in the last ${windowS} s adds ${extension}, at most 10 times.`,
    extended: (n) => (n === 1 ? 'Extended once.' : `Extended ${n} times.`),
    closedAt: (time) => `Closed at ${time}.`,
    sold: (alias, amount, sequence) => `Sold to ${alias} at ${amount}, bid №${sequence}.`,
    noSale: 'Closed without a sale.',
    noReserve: 'No reserve',
    reserveMet: 'Reserve met',
    reserveNotMet: 'Reserve not met',
    ledger: {
      caption: 'The newest bids of the run',
      sequence: '№',
      bidder: 'Bidder',
      amount: 'Amount',
      decided: 'Decided at',
      byMaximum: 'by maximum',
    },
    earlier: (n) => (n === 1 ? '1 earlier bid' : `${n} earlier bids`),
    again: 'Replay again',
    emptyTitle: "The demo hasn't run yet.",
    emptyBody:
      'When it runs, every bid appears here with its sequence number and the time Redis decided it. Until then you can browse the live and upcoming lots or start bidding.',
  },
} satisfies LandingCatalogue);
