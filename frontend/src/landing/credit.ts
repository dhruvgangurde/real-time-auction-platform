// A demo lot's photo credit (brief 7.4). Images carry no credit field yet (a later backend item,
// docs/decisions.md); the runner and the seed write the credit as the description's last
// paragraph, starting "Photo" (workers/demo_runner.py, app/demo.py). Anything else is no credit.
const CREDIT = /^Photo(?: of [^:\n]{1,60})?: [^\n]{1,160}$/;

export function photoCredit(description: string): string | null {
  const last = description.trim().split(/\n\s*\n/).at(-1)?.trim() ?? '';
  return CREDIT.test(last) ? last : null;
}
