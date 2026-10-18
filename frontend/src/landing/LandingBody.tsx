// The landing under its headline (brief 8): the hero, then the sections. Loaded lazily by
// app/Home.tsx: this chunk holds the landing's code and strings. Every request it makes is
// anonymous and goes through one scheduler (useLanding.ts); no socket is opened (FR-28).
import { useApp, useToken } from '../app/context';
import { Break } from './Break';
import { Hero } from './Hero';
import { useLandingApi, useNewestLive, useOverview } from './useLanding';

export default function LandingBody() {
  const { api } = useApp();
  const signedIn = useToken() !== null;
  const demo = useLandingApi(api);
  const [overview, refresh] = useOverview(demo);
  const live = useNewestLive(demo);
  return (
    <>
      <Hero demo={demo} overview={overview} live={live} signedIn={signedIn} />
      <Break demo={demo} overview={overview} refresh={refresh} />
    </>
  );
}
