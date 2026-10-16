// The account pages' strings, English (India) (brief 9.10). Amounts and times arrive formatted.
import { guardSentences, type AccountCatalogue } from './catalogue';

export const accountEnIN = guardSentences({
  title: 'Your account',
  profile: {
    title: 'Profile',
    name: 'Name',
    nameHint: 'Shown in the header, and to the seller of an auction you win.',
    save: 'Save name',
    saving: 'Saving…',
    saved: 'Saved.',
    email: 'Email',
    emailNote: 'You sign in with it; it cannot be changed here.',
    status: 'Account',
    roles: 'Roles',
    statuses: { pending_verification: 'Email not verified yet', active: 'Active', suspended: 'Suspended' },
    roleNames: { bidder: 'Bidder', seller: 'Seller', admin: 'Administrator' },
    problems: { required: 'Fill this in.', long: 'At most 200 characters.' },
  },
  links: { title: 'Your pages', bids: 'Your bids', listings: 'Your listings', admin: 'Administration', email: 'Email choices' },
  password: {
    title: 'Password',
    note: 'We email you a link to set a new password. Setting it signs you out everywhere, this tab included.',
    send: 'Email me a link',
    sending: 'Sending…',
    sent: (email) => `Sent to ${email}. The link works for an hour.`,
  },
  signOut: { title: 'Sign out', note: 'Signs out this browser. Other devices stay signed in.' },
} satisfies AccountCatalogue);
