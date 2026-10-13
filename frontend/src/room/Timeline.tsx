// The now-to-end timeline (UI-4, FR-55): the last window hatched, the time an extension added
// in marigold with the old end marked "was". Two widths, one shown per viewport (CSS).
import { formatLocal, formatRemaining, formatWindow } from '../lib/clock';
import { useStrings } from '../strings/catalogue';
import styles from './room.module.css';
import { rulerModel, type Ruler as RulerModel } from './time';

type Props = {
  nowMs: number;
  endMs: number;
  oldEndMs: number | null;
  windowMs: number;
  extensionMs: number;
  capped: boolean;
  /** the bid that added the time, for the key */
  sequence: number;
};

type Labels = { label: string; now: string; ends: string; was: string };

function Ruler({ model, label, now, ends, was }: { model: RulerModel } & Labels) {
  const w = model.width;
  const hatch = `hatch-${w}`;
  return (
    <svg viewBox={`0 0 ${w} 80`} role="img" aria-label={label}>
      <defs>
        <pattern id={hatch} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="7" stroke="var(--rule-strong)" strokeWidth="2.5" />
        </pattern>
      </defs>
      {model.zone ? <rect x={model.zone.x0} y="22" width={model.zone.x1 - model.zone.x0} height="28" fill={`url(#${hatch})`} /> : null}
      <line x1="0" y1="36" x2={w} y2="36" stroke="var(--ink)" strokeWidth="2" />
      {model.band ? <rect x={model.band.x0} y="31" width={model.band.x1 - model.band.x0} height="10" fill="var(--marigold)" /> : null}
      {model.ticks.map((t) => (
        <line
          key={t.x}
          x1={t.x}
          y1={t.long ? 26 : 30}
          x2={t.x}
          y2={t.long ? 46 : 42}
          stroke={t.onBand ? 'var(--on-marigold)' : 'var(--ink-3)'}
          strokeWidth="1.5"
        />
      ))}
      {model.was ? (
        <>
          <line x1={model.was.x} y1="18" x2={model.was.x} y2="54" stroke="var(--ink)" strokeWidth="1.5" strokeDasharray="3 3" />
          <text className={styles.rulerText} x={model.was.anchorEnd ? model.was.x - 6 : model.was.x + 6} y="12" textAnchor={model.was.anchorEnd ? 'end' : 'start'}>
            {was}
          </text>
        </>
      ) : null}
      <line x1="1.5" y1="14" x2="1.5" y2="58" stroke="var(--ink)" strokeWidth="3" />
      <text className={styles.rulerTextStrong} x="0" y="74" textAnchor="start">
        {now}
      </text>
      <text className={styles.rulerTextStrong} x={w} y="12" textAnchor="end">
        {ends}
      </text>
    </svg>
  );
}

export function Timeline({ nowMs, endMs, oldEndMs, windowMs, extensionMs, capped, sequence }: Props) {
  const s = useStrings();
  const c = s.roomUi.clock;
  const time = (ms: number) => formatLocal(ms, { seconds: true });
  const window = formatWindow(windowMs, s.roomUi.units);
  const extension = formatRemaining(extensionMs);
  const labels: Labels = {
    label: c.rulerLabel(time(endMs), window),
    now: c.now,
    ends: c.rulerEnds(time(endMs)),
    was: oldEndMs === null ? '' : c.rulerWas(time(oldEndMs)),
  };
  return (
    <div className={styles.ruler}>
      <div className={styles.rulerD}>
        <Ruler model={rulerModel(nowMs, endMs, oldEndMs, windowMs, capped, 520)} {...labels} />
      </div>
      <div className={styles.rulerM}>
        <Ruler model={rulerModel(nowMs, endMs, oldEndMs, windowMs, capped, 358)} {...labels} />
      </div>
      <p className={styles.rulerKey}>
        {capped ? null : (
          <span>
            <i className={styles.keyHatch} aria-hidden="true" />
            {c.keyLastMinute(window, extension)}
          </span>
        )}
        {oldEndMs === null ? null : (
          <span>
            <i className={styles.keyBand} aria-hidden="true" />
            {c.keyAdded(extension, sequence)}
          </span>
        )}
      </p>
    </div>
  );
}
