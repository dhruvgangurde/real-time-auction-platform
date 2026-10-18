// The four help topics' strings, English (India) (UD-4, brief 9.11): their own module, imported by
// the help, room and sell chunks only, so the entry does not carry them.
import { guardSentences, type HelpCatalogue } from './catalogue';

export const helpEnIN = guardSentences({
  title: 'How bidding works',
  lede: 'Four short answers to the questions the room raises. Every amount is in rupees.',
  close: 'Close help',
  openLabel: (title) => `Help: ${title}`,
  topics: {
    reserve: {
      title: 'The reserve price',
      question: 'What is a reserve?',
      body: [
        "A reserve is the seller's hidden minimum. If bidding ends below it, the lot is not sold and nobody pays.",
        'The room only ever says "Reserve not met" or "Reserve met". Nobody, not even the winner, sees the amount.',
      ],
      example: 'A camera starts at ₹9,000 with a hidden reserve. Bids reach ₹14,000 and the room switches to "Reserve met": from here, the highest bid at the close buys the lot.',
      diagram: 'A price scale from ₹9,000 to ₹22,000 with a hidden line somewhere in the middle; bids below it read "Reserve not met", bids above it "Reserve met".',
      labels: ['₹9,000', '₹22,000', 'Reserve not met', 'Reserve met', 'hidden'],
    },
    increment: {
      title: 'The minimum next bid',
      question: 'Why a minimum next bid?',
      body: [
        'Every auction has a step, the minimum increment. A new bid must be at least the current bid plus that step, so bidding moves in clear rounds instead of by a rupee at a time.',
        'The bid control always shows that minimum, prefilled, so a bid at the minimum is one keypress.',
      ],
      example: 'The current bid is ₹11,000 and the step is ₹500, so the next bid is at least ₹11,500. ₹11,200 is refused as too low; ₹12,000 is fine.',
      diagram: 'Three steps rising from ₹11,000 to ₹11,500 to ₹12,000, each step the size of the increment.',
      labels: ['₹11,000', '₹11,500', '₹12,000', '+₹500'],
    },
    maximum: {
      title: 'Your maximum stays private',
      question: 'Who sees my maximum?',
      body: [
        'Set a maximum and the auction bids for you: one step above each competing bid, never more than needed, up to your limit.',
        'Nobody else sees your maximum, not other bidders and not the seller. It is stored, used by the auction and shown back only to you.',
      ],
      example: 'You set a maximum of ₹16,000 while the bid is ₹13,000. Someone bids ₹13,500; the auction answers for you at ₹14,000. Nobody learns the ₹16,000 unless the bidding gets there.',
      diagram: 'A ladder of bids answered one step at a time, with your maximum as a dashed line above them marked "only you".',
      labels: ['₹13,500', '₹14,000', '₹16,000', 'only you'],
    },
    clock: {
      title: 'Why the clock moved',
      question: 'Why did the clock move?',
      body: [
        'A bid in the last minute adds two minutes, so everyone has time to answer. The clock can move at most ten times; after that it closes on time.',
        'The time comes from the server, never from your device. Every screen sees the same end time.',
      ],
      example: 'The auction ends at 20:30:00. A bid of ₹17,000 at 20:29:12 is inside the last minute, so it now ends at 20:32:00. Ten such bids would make the latest possible end 20:50:00.',
      diagram: 'A timeline ending at 20:30:00 with the last minute hatched; a bid inside it moves the end to 20:32:00, shown as a filled band.',
      labels: ['20:29:12', '20:30:00', '20:32:00', 'last minute', '+2:00'],
    },
  },
} satisfies HelpCatalogue);
