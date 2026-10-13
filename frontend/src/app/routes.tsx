import { createBrowserRouter } from 'react-router';

import { Home } from './Home';
import { NotFound } from './NotFound';
import { Shell } from './Shell';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: '*', element: <NotFound /> },
    ],
  },
]);
