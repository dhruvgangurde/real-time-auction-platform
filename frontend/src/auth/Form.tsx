// Native forms with the one error envelope (brief 13 item 29, FR-12): the catalogue message for
// the code at the top, field messages under their inputs, the first invalid field focused after
// a failed submit; the submit button stays enabled until the request starts.
import { useId, useState, type FormEvent, type ReactNode } from 'react';

import { ApiError } from '../api/client';
import { errorMessage, useStrings } from '../strings/catalogue';
import styles from './Form.module.css';

export type Submit = (data: FormData) => Promise<void>;

export function useForm(submit: Submit) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    setPending(true);
    setError(null);
    try {
      await submit(new FormData(form));
    } catch (e) {
      const failure = e instanceof ApiError ? e : new ApiError('INTERNAL_ERROR', '', 0);
      setError(failure);
      // after the re-render: the first field the server rejected
      setTimeout(() => form.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
    } finally {
      setPending(false);
    }
  }
  return { pending, error, fields: error?.fields ?? {}, onSubmit };
}

export function FormError({ error, children }: { error: ApiError | null; children?: ReactNode }) {
  const s = useStrings();
  if (error === null) return null;
  const text =
    error.code === 'RATE_LIMITED' && error.retryAfterMs !== undefined
      ? s.auth.signIn.rateLimited(Math.ceil(error.retryAfterMs / 1000))
      : errorMessage(s, error.code);
  return (
    <div className={styles.error} role="alert">
      <p>{text}</p>
      {children}
    </div>
  );
}

type FieldProps = {
  name: string;
  label: string;
  type: 'text' | 'email' | 'password';
  autoComplete: string;
  error?: string | undefined;
  hint?: string | undefined;
  minLength?: number;
  maxLength?: number;
  defaultValue?: string;
};

export function Field({ name, label, type, autoComplete, error, hint, minLength, maxLength, defaultValue }: FieldProps) {
  const id = useId();
  const described = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(' ');
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        name={name}
        type={type}
        autoComplete={autoComplete}
        spellCheck={type === 'email' ? false : undefined}
        required
        minLength={minLength}
        maxLength={maxLength}
        defaultValue={defaultValue}
        aria-invalid={error ? true : undefined}
        aria-describedby={described || undefined}
      />
      {hint ? (
        <p id={`${id}-hint`} className={styles.hint}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className={styles.fieldError}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
