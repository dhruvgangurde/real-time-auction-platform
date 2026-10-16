// The account page's rules (FR-9; brief 9.1, 9.10): which of the user's pages it links to by role,
// and what the API would refuse about a new name (ProfileUpdate: 1 to 200 characters, not trimmed
// by the server, so trimmed here). The server still decides.
export type AccountLink = 'bids' | 'listings' | 'admin' | 'email';

export function accountLinks(roles: readonly string[]): AccountLink[] {
  return [
    'bids',
    ...(roles.includes('seller') ? (['listings'] as const) : []),
    ...(roles.includes('admin') ? (['admin'] as const) : []),
    'email',
  ];
}

export function nameProblem(name: string): 'required' | 'long' | null {
  const trimmed = name.trim();
  if (trimmed === '') return 'required';
  return trimmed.length > 200 ? 'long' : null;
}
