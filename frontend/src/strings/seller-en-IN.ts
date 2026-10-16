// The seller screens' strings, English (India) (brief 9.8). Amounts and times arrive formatted.
import { guardSentences, type SellerCatalogue } from './catalogue';

export const sellerEnIN = guardSentences({
  problems: {
    required: 'Fill this in.',
    invalid: 'Check this value.',
    amount: 'Enter rupees, like 9,500 or 9,500.50.',
    startingMin: 'The starting price is at least ₹1.',
    incrementMin: 'The increment must be more than zero.',
    reserveBelowStart: 'The reserve cannot be below the starting price.',
    past: 'The start cannot be in the past.',
    endBeforeStart: 'The end must be after the start.',
    seconds: 'Enter whole seconds from 10 to 600.',
    category: 'That category no longer exists. Choose another.',
    images: 'Add at least one photo before publishing.',
  },
} satisfies SellerCatalogue);
