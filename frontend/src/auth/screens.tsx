// The identity screens (brief 9.10): register, check your email, verify, sign in, forgot and reset.
// One chunk: the routes import this module lazily. Every string is from the catalogue; every
// error is the envelope's code; the token only ever comes from the route loader (token.ts).
import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useLoaderData, useLocation, useNavigate } from 'react-router';

import { useApp, useToken } from '../app/context';
import { useStrings } from '../strings/catalogue';
import { Field, FormError, useForm } from './Form';
import { safeNext } from './next';
import styles from './screens.module.css';
import type { TokenLoader } from './token';

const text = (data: FormData, name: string) => String(data.get(name) ?? '').trim();

function Page({ title, lede, children }: { title: string; lede?: string; children: React.ReactNode }) {
  return (
    <section className={styles.card}>
      <h1 className={styles.title}>{title}</h1>
      {lede ? <p className={styles.lede}>{lede}</p> : null}
      {children}
    </section>
  );
}

function ResendForm({ email, title }: { email?: string; title?: string }) {
  const s = useStrings();
  const { api } = useApp();
  const [sent, setSent] = useState(false);
  const form = useForm(async (data) => {
    await api.resendVerification(text(data, 'email'));
    setSent(true);
  });
  return (
    <form onSubmit={form.onSubmit} noValidate>
      {title ? <h2 className={styles.lede}>{title}</h2> : null}
      <Field name="email" label={s.auth.register.email} type="email" autoComplete="email" defaultValue={email ?? ''} error={form.fields.email} />
      <FormError error={form.error} />
      {sent ? (
        <div className={styles.ok} role="status">
          <p>{s.auth.checkEmail.resent}</p>
        </div>
      ) : null}
      <div className={styles.actions}>
        <button className={styles.quiet} type="submit" disabled={form.pending}>
          {s.auth.checkEmail.resend}
        </button>
      </div>
    </form>
  );
}

export function Register() {
  const s = useStrings();
  const { api } = useApp();
  const navigate = useNavigate();
  const form = useForm(async (data) => {
    const email = text(data, 'email');
    await api.register(email, String(data.get('password') ?? ''), text(data, 'full_name'));
    void navigate('/check-your-email', { state: { email } });
  });
  if (useToken() !== null) return <Navigate to="/" replace />;
  return (
    <Page title={s.auth.register.title}>
      <form onSubmit={form.onSubmit} noValidate>
        <Field name="full_name" label={s.auth.register.name} type="text" autoComplete="name" maxLength={200} error={form.fields.full_name} />
        <Field name="email" label={s.auth.register.email} type="email" autoComplete="email" error={form.fields.email} />
        <Field
          name="password"
          label={s.auth.register.password}
          type="password"
          autoComplete="new-password"
          minLength={10}
          maxLength={128}
          hint={s.auth.register.passwordHint}
          error={form.fields.password}
        />
        <FormError error={form.error} />
        <div className={styles.actions}>
          <button className={styles.button} type="submit" disabled={form.pending}>
            {s.auth.register.submit}
          </button>
        </div>
      </form>
      <p className={styles.foot}>
        {s.auth.register.haveAccount} <Link to="/sign-in">{s.auth.register.signIn}</Link>
      </p>
    </Page>
  );
}

export function CheckYourEmail() {
  const s = useStrings();
  const email = (useLocation().state as { email?: string } | null)?.email;
  return (
    <Page title={s.auth.checkEmail.title} lede={email ? s.auth.checkEmail.body(email) : s.auth.checkEmail.bodyNoEmail}>
      <ResendForm {...(email ? { email } : {})} />
    </Page>
  );
}

export function VerifyEmail() {
  const s = useStrings();
  const { api } = useApp();
  const { token } = useLoaderData() as TokenLoader;
  const [state, setState] = useState<'verifying' | 'done' | 'failed'>('verifying');
  const [code, setCode] = useState<string>('INTERNAL_ERROR');
  useEffect(() => {
    if (token === null) return;
    let live = true;
    api
      .verifyEmail(token)
      .then(() => live && setState('done'))
      .catch((e: { code?: string }) => {
        if (!live) return;
        setCode(e.code ?? 'INTERNAL_ERROR');
        setState('failed');
      });
    return () => {
      live = false;
    };
  }, [api, token]);
  if (token === null) {
    return (
      <Page title={s.auth.verify.title} lede={s.auth.verify.incomplete}>
        <ResendForm title={s.auth.verify.resendTitle} />
      </Page>
    );
  }
  if (state === 'done') {
    return (
      <Page title={s.auth.verify.title}>
        <div className={styles.ok} role="status">
          <p>{s.auth.verify.done}</p>
        </div>
        <div className={styles.actions}>
          <Link className={styles.button} to="/sign-in">
            {s.auth.verify.signIn}
          </Link>
        </div>
      </Page>
    );
  }
  if (state === 'failed') {
    return (
      <Page title={s.auth.verify.title}>
        <div className={styles.lede} role="alert">
          {s.errors[code as keyof typeof s.errors] ?? s.unknownError}
        </div>
        <ResendForm title={s.auth.verify.resendTitle} />
      </Page>
    );
  }
  return (
    <Page title={s.auth.verify.title}>
      <p role="status">{s.auth.verify.verifying}</p>
    </Page>
  );
}

export function SignIn() {
  const s = useStrings();
  const { api, auth } = useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/';
  // ?next= from another document (the payment page): a full load back to it, only to a path here
  const next = safeNext(new URLSearchParams(location.search).get('next'));
  const token = useToken();
  const [email, setEmail] = useState('');
  const form = useForm(async (data) => {
    const address = text(data, 'email');
    setEmail(address);
    const tokens = await api.login(address, String(data.get('password') ?? ''));
    auth.accept(tokens.access_token, tokens.expires_in);
    if (next === null) void navigate(from, { replace: true });
  });
  useEffect(() => {
    if (token !== null && next !== null) window.location.replace(next);
  }, [token, next]);
  if (token !== null) return next === null ? <Navigate to={from} replace /> : null;
  return (
    <Page title={s.auth.signIn.title}>
      <form onSubmit={form.onSubmit} noValidate>
        <Field name="email" label={s.auth.signIn.email} type="email" autoComplete="email" error={form.fields.email} />
        <Field name="password" label={s.auth.signIn.password} type="password" autoComplete="current-password" maxLength={128} error={form.fields.password} />
        <FormError error={form.error}>
          {form.error?.code === 'EMAIL_NOT_VERIFIED' ? (
            <Link to="/check-your-email" state={{ email }}>
              {s.auth.signIn.notVerifiedResend}
            </Link>
          ) : null}
        </FormError>
        <div className={styles.actions}>
          <button className={styles.button} type="submit" disabled={form.pending}>
            {s.auth.signIn.submit}
          </button>
          <Link to="/forgot-password">{s.auth.signIn.forgot}</Link>
        </div>
      </form>
      <p className={styles.foot}>
        {s.auth.signIn.noAccount} <Link to="/register">{s.auth.signIn.register}</Link>
      </p>
    </Page>
  );
}

export function ForgotPassword() {
  const s = useStrings();
  const { api } = useApp();
  const [done, setDone] = useState(false);
  const form = useForm(async (data) => {
    await api.forgotPassword(text(data, 'email'));
    setDone(true);
  });
  return (
    <Page title={s.auth.forgot.title} lede={s.auth.forgot.body}>
      {done ? (
        <div className={styles.ok} role="status">
          <p>{s.auth.forgot.done}</p>
        </div>
      ) : (
        <form onSubmit={form.onSubmit} noValidate>
          <Field name="email" label={s.auth.forgot.email} type="email" autoComplete="email" error={form.fields.email} />
          <FormError error={form.error} />
          <div className={styles.actions}>
            <button className={styles.button} type="submit" disabled={form.pending}>
              {s.auth.forgot.submit}
            </button>
          </div>
        </form>
      )}
    </Page>
  );
}

export function ResetPassword() {
  const s = useStrings();
  const { api } = useApp();
  const { token } = useLoaderData() as TokenLoader;
  const [done, setDone] = useState(false);
  const form = useForm(async (data) => {
    if (token === null) return;
    await api.resetPassword(token, String(data.get('password') ?? ''));
    setDone(true);
  });
  if (token === null) {
    return (
      <Page title={s.auth.reset.title} lede={s.auth.reset.incomplete}>
        <div className={styles.actions}>
          <Link className={styles.button} to="/sign-in">
            {s.auth.reset.signIn}
          </Link>
        </div>
      </Page>
    );
  }
  return (
    <Page title={s.auth.reset.title}>
      {done ? (
        <>
          <div className={styles.ok} role="status">
            <p>{s.auth.reset.done}</p>
          </div>
          <div className={styles.actions}>
            <Link className={styles.button} to="/sign-in">
              {s.auth.reset.signIn}
            </Link>
          </div>
        </>
      ) : (
        <form onSubmit={form.onSubmit} noValidate>
          <Field
            name="password"
            label={s.auth.reset.password}
            type="password"
            autoComplete="new-password"
            minLength={10}
            maxLength={128}
            hint={s.auth.register.passwordHint}
            error={form.fields.password}
          />
          <FormError error={form.error} />
          <div className={styles.actions}>
            <button className={styles.button} type="submit" disabled={form.pending}>
              {s.auth.reset.submit}
            </button>
          </div>
        </form>
      )}
    </Page>
  );
}

/** The header's sign-out control; a false result keeps the page and says so. */
export function SignOutButton({ className }: { className?: string | undefined }) {
  const s = useStrings();
  const { auth } = useApp();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);
  async function onClick(event: FormEvent<HTMLButtonElement>) {
    event.preventDefault();
    const ok = await auth.logout();
    setFailed(!ok);
    if (ok) void navigate('/');
  }
  return (
    <>
      <button type="button" className={className} onClick={onClick}>
        {s.auth.signOut.label}
      </button>
      {failed ? <span role="alert">{s.auth.signOut.failed}</span> : null}
    </>
  );
}
