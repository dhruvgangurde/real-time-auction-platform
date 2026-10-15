// The SPA's document (index.html). The payment page is a document of its own (pay/main.tsx).
import { RouterProvider } from 'react-router';

import { boot } from './app/boot';
import { router } from './app/routes';

boot(<RouterProvider router={router} />);
