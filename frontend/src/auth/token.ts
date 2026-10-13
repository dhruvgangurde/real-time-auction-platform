// brief 13 item 23: the emailed links carry the token in the fragment (#token=…). The route
// loader takes it out of the URL with history.replaceState before anything renders, so it is
// never in history, a referrer, or a request line; it travels only in the API body.

export type TokenLoader = { token: string | null };

export function takeTokenFromHash(
  loc: { hash: string; pathname: string; search: string } = location,
  replace: (url: string) => void = (url) => history.replaceState(history.state, '', url),
): TokenLoader {
  const match = /(?:^#|[#&])token=([A-Za-z0-9_-]+)/.exec(loc.hash);
  if (loc.hash !== '') replace(loc.pathname + loc.search); // strip the fragment either way
  return { token: match?.[1] ?? null };
}
