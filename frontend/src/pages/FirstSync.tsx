import { useState } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router'
import { ApiError } from '../api/client'
import { useProviders, useRuns, useStartSync, useSyncPreview } from '../api/hooks'
import type { Change, Me, MirrorResult } from '../api/types'
import { Icon } from '../ui/Icon'
import { isRunning } from '../format'
import { finishSetup } from '../setup'
import { PreviewLoading } from '../ui/Loading'
import { OnboardingLayout, RequireUser } from '../ui/Onboarding'
import { ProviderChip } from '../ui/Provider'
import { Topbar } from '../ui/Shell'

const TAG = { create: ['tag-ok', 'Add'], update: ['tag-info', 'Update'], delete: ['tag-err', 'Remove'] } as const
type Filter = 'all' | Change['op']

function Stat({ n, label, warn }: { n: number; label: string; warn?: boolean }) {
  return (
    <div className="card" style={{ padding: '16px 20px' }}>
      <div className="headline-l num" style={warn && n > 0 ? { color: 'var(--warning)' } : undefined}>{n}</div>
      <div className="body-m muted">{label}</div>
    </div>
  )
}

function Mirror({ result, prune, name }: { result: MirrorResult; prune: boolean; name: string }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [all, setAll] = useState(false)
  const changes = (result.changes ?? []).filter((c) => filter === 'all' || c.op === filter)
  const shown = all ? changes : changes.slice(0, 20)
  return (
    <>
      <div className="grid-stats" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
        <Stat n={result.create} label="titles to add" />
        <Stat n={result.update} label="entries to update" />
        <Stat n={result.delete} label={prune ? 'to remove' : 'to remove (removal is off)'} />
        <Stat n={result.unmapped} label="couldn't be matched" warn />
      </div>
      <section className="card-out" style={{ overflow: 'hidden' }}>
        <div style={{ padding: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <h2 className="title-m">Changes to {name}</h2>
          <div className="chip-row" role="group" aria-label="Filter changes">
            {(['all', 'create', 'update', 'delete'] as const).map((f) => (
              <button key={f} type="button" className={`chip${filter === f ? ' chip-on' : ''}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {filter === f && <Icon name="check" size={18} />} {f === 'all' ? 'All' : TAG[f][1] + 's'}
              </button>
            ))}
          </div>
        </div>
        {shown.length === 0 ? (
          <p className="body-m muted" style={{ padding: 16, borderTop: '1px solid var(--outline-variant)' }}>Nothing to change here.</p>
        ) : (
          <div className="list" style={{ borderTop: '1px solid var(--outline-variant)' }}>
            {shown.map((c) => (
              <div key={`${c.op}-${c.media_id}`} className="list-item">
                <div className="list-text">
                  <span className="title-m">{c.title}</span>
                  <span className="body-m muted">{c.fields.length ? `Changes: ${c.fields.join(', ')}` : c.op === 'create' ? 'New entry' : 'Entry removed'}</span>
                </div>
                <span className={`tag ${TAG[c.op][0]}`} style={{ minWidth: 64, justifyContent: 'center' }}>{TAG[c.op][1]}</span>
              </div>
            ))}
          </div>
        )}
        {!all && changes.length > shown.length && (
          <div style={{ padding: 8, borderTop: '1px solid var(--outline-variant)' }}>
            <button type="button" className="btn btn-text" onClick={() => setAll(true)}>Show all {changes.length} changes</button>
          </div>
        )}
      </section>
      {!!result.adult_skipped && (
        <p className="body-m muted">{result.adult_skipped} adult {result.adult_skipped === 1 ? 'title is' : 'titles are'} not copied to {name}, as you chose.</p>
      )}
      {result.unmapped > 0 && (
        <details className="card" style={{ padding: '16px 20px' }}>
          <summary className="title-s" style={{ cursor: 'pointer', minHeight: 28 }}>
            {result.unmapped} {result.unmapped === 1 ? 'title has' : 'titles have'} no {name} match and will be skipped
          </summary>
          <p className="body-m muted" style={{ marginTop: 8 }}>
            These exist on your master but {name} has no matching entry, usually music videos or very new titles. They're retried on every sync.
          </p>
          {result.unmapped_titles.length > 0 && (
            <ul className="body-m" style={{ margin: '8px 0 0', paddingLeft: 20 }}>{result.unmapped_titles.map((t) => <li key={t}>{t}</li>)}</ul>
          )}
        </details>
      )}
    </>
  )
}

/** The review of what a sync would change. `setup`: step 3 of the first-time flow; `preview`: from the dashboard. */
function Review({ me, mode }: { me: Me; mode: 'setup' | 'preview' }) {
  const providers = useProviders()
  const preview = useSyncPreview()
  const runs = useRuns()
  const start = useStartSync()
  const navigate = useNavigate()
  const [tab, setTab] = useState<string | null>(null)
  const name = (id: string) => providers.data?.find((p) => p.id === id)?.name ?? id
  const master = me.accounts.find((a) => a.is_master)
  const results = preview.data ? Object.values(preview.data) : []
  const current = results.find((r) => r.provider === tab) ?? results[0]
  const failed = results.filter((r) => r.error)
  const syncRunning = (runs.data ?? []).some(isRunning)

  if (!providers.data) return <p className="body-l muted" style={{ padding: 24 }} role="status">Loading…</p>
  const loadError = preview.error instanceof ApiError ? preview.error.message : preview.isError ? "Couldn't reach AniSyncHub." : null
  const setup = mode === 'setup'

  const content = (
    <>
      {setup ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h1 className="headline-l">Here's what will change</h1>
          <p className="body-l muted">
            Copying from <strong style={{ color: 'var(--on-surface)' }}>{master ? name(master.provider) : 'your master'}</strong>. Nothing is written until you apply.
          </p>
        </div>
      ) : (
        <p className="body-l muted">
          Copying from <strong style={{ color: 'var(--on-surface)' }}>{master ? name(master.provider) : 'your master'}</strong>. Nothing is written until you sync.
        </p>
      )}
      {syncRunning && (
        <div className="banner banner-info" role="status" style={{ borderRadius: 16, padding: '16px 20px' }}>
          <Icon name="sync" size={20} style={{ flex: 'none' }} />
          <span className="body-m" style={{ flex: '1 1 260px' }}>A sync is running right now. You can look at the preview, but starting another is on hold until it finishes.</span>
        </div>
      )}
      {preview.isFetching && (
        <PreviewLoading master={master?.provider} mirrors={me.accounts.filter((a) => !a.is_master && a.sync_enabled).map((a) => a.provider)} />
      )}
      {!preview.isFetching && loadError && (
        <div className="banner banner-warn" role="alert" style={{ borderRadius: 16, padding: '16px 20px' }}>
          <span className="body-m">{loadError}</span>
          <button type="button" className="btn btn-text" onClick={() => preview.refetch()}>Try again</button>
        </div>
      )}
      {!preview.isFetching && results.length > 1 && (
        <div className="tabs" role="tablist" aria-label="Mirror">
          {results.map((r) => (
            <button key={r.provider} type="button" role="tab" className="tab" aria-selected={r.provider === current?.provider} onClick={() => setTab(r.provider)}>
              <ProviderChip id={r.provider} size={24} /> {name(r.provider)} · {r.create + r.update + r.delete}
            </button>
          ))}
        </div>
      )}
      {!preview.isFetching && failed.map((r) => (
        <p key={r.provider} className="body-m" role="alert" style={{ color: 'var(--error)' }}>
          {name(r.provider)}: {r.reauth_required ? 'the connection expired. Reconnect it on the link page.' : r.error}
        </p>
      ))}
      {!preview.isFetching && current && !current.error && (
        <Mirror result={current} prune={!!me.accounts.find((a) => a.provider === current.provider)?.prune_missing} name={name(current.provider)} />
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <Link to={setup ? '/master' : '/dashboard'} className="btn btn-text"><Icon name="arrow-left" size={18} /> {setup ? 'Back' : 'Back to dashboard'}</Link>
        <button
          type="button"
          className="btn btn-filled"
          disabled={!preview.data || preview.isFetching || failed.length > 0 || start.isPending || syncRunning}
          onClick={() => start.mutate(undefined, { onSuccess: () => { finishSetup(); navigate('/dashboard') } })}
        >
          {start.isPending ? 'Starting…' : syncRunning ? 'A sync is running…' : `Apply to ${results.map((r) => name(r.provider)).join(' and ') || 'mirrors'}`}
        </button>
      </div>
      {start.isError && (
        <p className="body-m" role="alert" style={{ color: 'var(--error)' }}>
          {start.error instanceof ApiError ? start.error.message : "Couldn't start the sync. Try again."}
        </p>
      )}
    </>
  )

  if (setup) return <OnboardingLayout step={3} wide>{content}</OnboardingLayout>
  return (
    <>
      <Topbar title="Preview changes" me={me} />
      <main className="page" style={{ paddingTop: 0, maxWidth: 960 }}>{content}</main>
    </>
  )
}

/** Step 3 of the first-time setup. */
export const FirstSync = () => <RequireUser>{(me) => <Review me={me} mode="setup" />}</RequireUser>

/** The same review, reached from the dashboard (inside the app, not the setup stepper). */
export function PreviewChanges() {
  const me = useOutletContext<Me>()
  return <Review me={me} mode="preview" />
}
