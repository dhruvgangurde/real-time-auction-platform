// Smoke check of the load-test tooling: one minted bidder's socket through the web service,
// authenticated with its token and subscribed to the load-test lot. Fails (exit 99) otherwise.
import { check } from 'k6';
import { WebSocket } from 'k6/websockets';

const plan = JSON.parse(open('/loadtest/.run/plan.json'));
const WS_URL = __ENV.WS_URL || 'ws://web/api/v1/ws';

export const options = { vus: 1, iterations: 1, thresholds: { checks: ['rate==1'] } };

export default function () {
  const me = plan.bidders[0];
  const ws = new WebSocket(WS_URL);
  ws.onopen = () => ws.send(JSON.stringify({ type: 'authenticate', token: me.token }));
  ws.onmessage = (event) => {
    const frame = JSON.parse(event.data);
    if (frame.type === 'authenticated') {
      check(frame, { 'authenticated as the minted bidder': (f) => f.user_id === me.id });
      ws.send(JSON.stringify({ type: 'subscribe', auction_id: plan.auction_id }));
    } else if (frame.type === 'subscribed') {
      check(frame, { 'subscribed to the load-test lot': (f) => f.auction_id === plan.auction_id });
      ws.close();
    } else if (frame.type === 'error') {
      check(frame, { 'no error frame': () => false });
      console.error(JSON.stringify(frame));
      ws.close();
    }
  };
}
