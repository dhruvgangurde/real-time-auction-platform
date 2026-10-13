// Routes and their chunks (brief 11.5): the shell, Home and NotFound are in the entry; every
// other screen loads through a dynamic import, so the entry never holds room, socket or reducer
// code (src/app/chunks.test.ts). The token loaders run before anything renders (item 23).
import { createBrowserRouter } from 'react-router';

import { takeTokenFromHash } from '../auth/token';
import { Home } from './Home';
import { NotFound } from './NotFound';
import { Shell } from './Shell';

const auth = (name: 'Register' | 'CheckYourEmail' | 'VerifyEmail' | 'SignIn' | 'ForgotPassword' | 'ResetPassword') => () =>
  import('../auth/screens').then((m) => ({ Component: m[name] }));

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: 'register', lazy: auth('Register') },
      { path: 'check-your-email', lazy: auth('CheckYourEmail') },
      { path: 'verify-email', lazy: auth('VerifyEmail'), loader: () => takeTokenFromHash() },
      { path: 'sign-in', lazy: auth('SignIn') },
      { path: 'forgot-password', lazy: auth('ForgotPassword') },
      { path: 'reset-password', lazy: auth('ResetPassword'), loader: () => takeTokenFromHash() },
      { path: '*', element: <NotFound /> },
    ],
  },
]);
