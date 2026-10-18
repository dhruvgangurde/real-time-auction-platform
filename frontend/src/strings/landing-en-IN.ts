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
} satisfies LandingCatalogue);
