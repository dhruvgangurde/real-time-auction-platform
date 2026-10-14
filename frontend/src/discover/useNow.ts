// One tick per page, not one per card: the server clock read once a second so every countdown on
// the page repaints together (UI-4: the time comes from the server, the tick only repaints).
import { useEffect, useState } from 'react';

import type { ServerClock } from '../lib/clock';

export function useNow(clock: ServerClock): number {
  const [now, setNow] = useState(() => clock.now());
  useEffect(() => {
    const id = setInterval(() => setNow(clock.now()), 1000);
    return () => clearInterval(id);
  }, [clock]);
  return now;
}
