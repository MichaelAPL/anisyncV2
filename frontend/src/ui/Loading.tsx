import { useEffect, useState } from 'react'
import { ProviderChip } from './Provider'

const STEPS = ['Reading your master list…', 'Matching titles across sites…', 'Reading your mirrors…', 'Comparing the lists…', 'Almost there…']

/** Loading view for the first-sync preview: the master hands a token to each mirror while the lists are read. */
export function PreviewLoading({ master, mirrors }: { master: string | undefined; mirrors: string[] }) {
  const [step, setStep] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 4500)
    return () => clearInterval(t)
  }, [])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }} role="status" aria-live="polite">
      <section className="card" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {master && <span className="pulse" style={{ borderRadius: 14, display: 'inline-flex' }}><ProviderChip id={master} size={52} /></span>}
          <span className="hop-track" aria-hidden="true"><span className="hop-dot" /></span>
          <span style={{ display: 'flex', gap: 8 }}>{mirrors.map((m) => <ProviderChip key={m} id={m} size={52} />)}</span>
        </div>
        <div className="bar-indeterminate" aria-hidden="true" />
        <div>
          <p className="title-m">{STEPS[step]}</p>
          <p className="body-m muted">Long lists can take a minute. Nothing is written yet.</p>
        </div>
      </section>
      <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="grid-stats" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 88, animationDelay: `${i * 120}ms` }} />)}
        </div>
        <div className="card-out" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <span className="skeleton" style={{ width: 40, height: 56, flex: 'none', animationDelay: `${i * 150}ms` }} />
              <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <span className="skeleton" style={{ height: 14, width: `${70 - i * 8}%`, animationDelay: `${i * 150}ms` }} />
                <span className="skeleton" style={{ height: 12, width: `${45 - i * 4}%`, animationDelay: `${i * 150 + 80}ms` }} />
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/** Short wait while names the screen needs (like the providers' display names) arrive. */
export function Loading() {
  return <p className="body-l muted" style={{ padding: 24 }} role="status">Loading…</p>
}
