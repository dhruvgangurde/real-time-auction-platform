// The payment document (pay.html; brief 13 item 25): served by nginx for /auctions/<id>/pay with its
// own CSP, the only one that allows Razorpay. No router and no socket: the SPA's code is not here.
import { boot } from '../app/boot';
import { PayPage } from './PayPage';

boot(<PayPage />);
