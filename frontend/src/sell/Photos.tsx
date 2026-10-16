// A listing's photos (brief 9.8; FR-13, NFR-24, HI-2, D10): the stored ones in their order, the
// first being the cover (there is no reorder, gap S2), added one at a time with a local preview
// (a blob: URL, allowed by img-src) and checked first as the server would, removed after a
// confirmation. A listing that left draft keeps one; the server still decides everything.
import { useEffect, useId, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { useApp } from '../app/context';
import styles from '../discover/discover.module.css';
import type { AuctionStatus } from '../lib/contract';
import { ConfirmDialog } from '../room/ConfirmDialog';
import { errorMessage, useStrings } from '../strings/catalogue';
import { sellerEnIN as t } from '../strings/seller-en-IN';
import { deleteImage, uploadImage } from './api';
import { TYPES, precheck } from './images';
import type { Editable } from './listing';
import own from './sell.module.css';

type Upload = { key: string; file: File; preview: string; state: 'waiting' | 'uploading' | 'failed'; code: string | null };
const codeOf = (e: unknown) => (e instanceof ApiError ? e.code : 'SERVICE_UNAVAILABLE');

export function Photos({
  id,
  images,
  status,
  can,
  onChanged,
}: {
  id: string;
  images: { id: number; url: string; position: number }[];
  status: AuctionStatus;
  can: Editable;
  onChanged: () => Promise<unknown>;
}) {
  const s = useStrings();
  const ph = t.photos;
  const { api } = useApp();
  const inputId = useId();
  const sorted = [...images].sort((a, b) => a.position - b.position || a.id - b.id);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [removing, setRemoving] = useState<number | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const previews = useRef(new Set<string>());
  useEffect(() => {
    const urls = previews.current;
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, []);
  const busy = uploads.some((u) => u.state !== 'failed');
  const changeable = can !== 'none';
  const keepsOne = status !== 'draft' && sorted.length <= 1; // FR-13: it left draft with a photo
  const update = (key: string, change: Partial<Upload>) => setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...change } : u)));

  async function add(files: File[]) {
    let attached = sorted.length;
    const next = files.map((file): Upload => {
      const code = precheck(file, attached);
      if (code === null) attached += 1;
      const preview = URL.createObjectURL(file);
      previews.current.add(preview);
      return { key: crypto.randomUUID(), file, preview, state: code === null ? 'waiting' : 'failed', code };
    });
    setUploads(next);
    for (const upload of next) {
      if (upload.state !== 'waiting') continue;
      update(upload.key, { state: 'uploading' });
      try {
        await uploadImage(api, id, upload.file);
        await onChanged();
        setUploads((list) => list.filter((u) => u.key !== upload.key));
      } catch (e) {
        update(upload.key, { state: 'failed', code: codeOf(e) });
      }
    }
  }

  async function remove(index: number) {
    const image = sorted[index];
    if (image === undefined) return;
    try {
      await deleteImage(api, id, image.id);
      await onChanged();
      setRemoving(null);
    } catch (e) {
      setRemoveError(errorMessage(s, codeOf(e)));
    }
  }

  return (
    <section className={own.photos} aria-labelledby={`${inputId}-title`}>
      <h2 id={`${inputId}-title`} className={own.sectionTitle}>
        {ph.title}
      </h2>
      {changeable ? <p className={styles.hint}>{ph.cover}</p> : null}
      {sorted.length === 0 ? <p className={styles.lede}>{ph.none}</p> : null}
      <ul className={own.thumbs}>
        {sorted.map((image, i) => (
          <li key={image.id} className={own.thumb}>
            <img src={image.url} alt={ph.alt(i + 1, sorted.length)} width={160} height={120} />
            {i === 0 ? <span className={own.tag}>{ph.coverTag}</span> : null}
            {changeable && !keepsOne ? (
              <button
                type="button"
                className={styles.btnQuiet}
                aria-label={ph.removeLabel(i + 1, sorted.length)}
                onClick={() => {
                  setRemoveError(null);
                  setRemoving(i);
                }}
              >
                {ph.remove}
              </button>
            ) : null}
          </li>
        ))}
        {uploads.map((u) => (
          <li key={u.key} className={own.thumb} data-state={u.state}>
            {/* a refused file may not be an image at all: no preview for it */}
            {u.state === 'failed' ? <div className={own.blank} /> : <img src={u.preview} alt={ph.preview(u.file.name)} width={160} height={120} />}
            <span className={u.state === 'failed' ? styles.fieldError : styles.hint} role={u.state === 'failed' ? 'alert' : 'status'}>
              {u.state === 'failed' ? ph.failed(u.file.name, errorMessage(s, u.code ?? 'INTERNAL_ERROR')) : u.state === 'uploading' ? ph.uploading(u.file.name) : ph.waiting(u.file.name)}
            </span>
          </li>
        ))}
      </ul>
      {changeable && keepsOne && sorted.length === 1 ? <p className={styles.hint}>{ph.keepOne}</p> : null}
      {changeable ? (
        <div className={styles.field}>
          <label className={styles.label} htmlFor={inputId}>
            {ph.add}
          </label>
          <input
            id={inputId}
            className={own.file}
            type="file"
            accept={TYPES.join(',')}
            multiple
            disabled={busy}
            aria-describedby={`${inputId}-hint`}
            onChange={(e) => {
              const files = [...(e.target.files ?? [])];
              e.target.value = ''; // the same file can be chosen again after a failure
              if (files.length > 0) void add(files);
            }}
          />
          <p id={`${inputId}-hint`} className={styles.hint}>
            {ph.addHint}
          </p>
        </div>
      ) : null}
      {removing === null ? null : (
        <ConfirmDialog
          title={ph.removeTitle(removing + 1, sorted.length)}
          body={<p>{ph.removeBody}</p>}
          confirmLabel={ph.remove}
          dismissLabel={ph.keep}
          error={removeError}
          onConfirm={() => void remove(removing)}
          onClose={() => setRemoving(null)}
        />
      )}
    </section>
  );
}
