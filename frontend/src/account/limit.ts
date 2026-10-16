// At most `n` tasks at once, the rest in order behind them (Q3 of the slice 4 plan: the title
// lookups of "Your bids"). A task that fails frees its place like one that succeeds.
export function limiter(n: number) {
  let active = 0;
  const waiting: (() => void)[] = [];
  return async function run<T>(task: () => Promise<T>): Promise<T> {
    while (active >= n) await new Promise<void>((resolve) => waiting.push(resolve));
    active += 1;
    try {
      return await task();
    } finally {
      active -= 1;
      waiting.shift()?.();
    }
  };
}
