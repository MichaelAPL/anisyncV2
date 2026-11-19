import { errText } from './util'

export function Err({ error }: { error: unknown }) {
  const m = errText(error)
  return m ? <p className="body-m" role="alert" style={{ color: 'var(--error)' }}>{m}</p> : null
}

export const Stat = ({ n, label, warn }: { n: number | string; label: string; warn?: boolean }) => (
  <div className="card" style={{ padding: '16px 20px' }}>
    <div className="headline-l num" style={warn ? { color: 'var(--warning)' } : undefined}>{n}</div>
    <div className="body-m muted">{label}</div>
  </div>
)

export const Loading = ({ q }: { q: { isPending: boolean; error: unknown } }) =>
  q.isPending ? <p className="body-m muted" role="status">Loading…</p> : q.error ? <Err error={q.error} /> : null

export const Card = ({ title, sub, right, children, id }: { title: string; sub?: string; right?: React.ReactNode; children?: React.ReactNode; id: string }) => (
  <section className="card-out" aria-labelledby={id} style={{ overflow: 'hidden' }}>
    <div style={{ padding: '16px 16px 8px', display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div style={{ flex: '1 1 240px' }}><h2 id={id} className="title-l">{title}</h2>{sub && <p className="body-m muted">{sub}</p>}</div>
      {right}
    </div>
    {children}
  </section>
)

