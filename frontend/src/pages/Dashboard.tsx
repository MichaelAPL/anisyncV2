import { useEffect, useState } from 'react'
import { Link, Navigate, useOutletContext } from 'react-router'
import { ApiError } from '../api/client'
import { useProviders, useRuns, useStartSync } from '../api/hooks'
import type { Account, Me, MirrorResult, SyncRun } from '../api/types'
import { clock, isRunning, relative, utcHour } from '../format'
import { Icon } from '../ui/Icon'
import { ProviderChip } from '../ui/Provider'
import { Loading } from '../ui/Loading'
import { Topbar } from '../ui/Shell'

import { setupDone } from '../setup'
const TRIGGER: Record<string, string> = { scheduled: 'Daily sync', manual: 'Sync now', edit: 'Edit', preview: 'Preview' }

function summarize(run: SyncRun, name: (id: string) => string): string {
  const parts = Object.values(run.summary).map((r) => {
    const bits = [r.create && `${r.create} added`, r.update && `${r.update} updated`, r.delete && `${r.delete} removed`].filter(Boolean)
    return `${name(r.provider)}: ${bits.length ? bits.join(', ') : 'no changes'}${r.error ? ' (failed)' : ''}`
  })
  return parts.join(' · ') || 'No changes needed'
}

function failures(r: MirrorResult | undefined): number {
  return r ? (r.errors?.length ?? 0) + (r.error ? 1 : 0) : 0
}

export function Dashboard() {
  const me = useOutletContext<Me>()
  const providers = useProviders()
  const [clicked, setClicked] = useState<number | null>(null)
  const [expired, setExpired] = useState(false)
  const runs = useRuns(clicked !== null && !expired)
  const start = useStartSync()
  const name = (id: string) => providers.data?.find((p) => p.id === id)?.name ?? id

  const latest = runs.data?.[0]
  const running = (runs.data ?? []).find(isRunning)
  const runningNow = running !== undefined
  // After a click there can be a short gap before the run row exists: stay busy until a run since the click has finished.
  const finishedSinceClick = clicked !== null && !!latest?.finished_at && new Date(latest.started_at).getTime() >= clicked
  const syncing = runningNow || (clicked !== null && !expired && !finishedSinceClick)
  useEffect(() => {
    if (clicked === null) return
    const t = setTimeout(() => setExpired(true), 60_000)
    return () => clearTimeout(t)
  }, [clicked])

  // New users are walked through linking their lists until they've synced once or chosen to skip.
  const setupComplete = setupDone() || me.accounts.some((a) => a.last_synced_at)
  if (!setupComplete && me.accounts.length < (providers.data?.length ?? 2)) return <Navigate to="/link" replace />

  if (!providers.data) return <Loading />

  const master: Account | undefined = me.accounts.find((a) => a.is_master)
  const mirrors = me.accounts.filter((a) => !a.is_master)
  const attention = me.accounts.find((a) => a.needs_relink || a.expires_soon)
  const lastSummary = runs.data?.find((r) => r.trigger !== 'preview')?.summary ?? {}

  return (
    <>
      <Topbar title="Dashboard" me={me} />
      <main className="page" style={{ paddingTop: 0 }}>
        {attention && (
          <div className="card" role="alert" style={{ padding: '16px 20px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', background: 'var(--warning-container)', color: 'var(--on-warning-container)' }}>
            <Icon name="clock" size={24} style={{ flex: 'none' }} />
            <div style={{ flex: '1 1 300px' }}>
              <p className="title-s">
                {attention.needs_relink ? `Your ${name(attention.provider)} connection has expired` : `Your ${name(attention.provider)} connection expires ${relative(attention.expires_at)}`}
              </p>
              <p className="body-m">{name(attention.provider)} doesn't allow automatic renewal, so reconnect to keep syncing.</p>
            </div>
            <a href={`/auth/${attention.provider}/login`} className="btn btn-filled" style={{ background: 'var(--on-warning-container)', color: 'var(--warning-container)' }}>
              Reconnect {name(attention.provider)}
            </a>
          </div>
        )}

        {master && (
          <section className="card-primary" style={{ padding: 24, display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
            <ProviderChip id={master.provider} size={64} />
            <div style={{ flex: '1 1 280px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span className="label-m" style={{ opacity: 0.8 }}>MASTER LIST</span>
              <h2 className="headline-m">{name(master.provider)} · {master.remote_username}</h2>
              <p className="body-m">{me.next_sync_at ? `Next sync today around ${utcHour(me.next_sync_at)}` : 'Not scheduled'}</p>
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <Link to="/review" className="btn btn-outlined" style={{ borderColor: 'var(--on-primary-container)', color: 'var(--on-primary-container)' }}>
                <Icon name="eye" size={18} /> Preview changes
              </Link>
              <button type="button" className="btn btn-filled" disabled={start.isPending || syncing}
                onClick={() => start.mutate(undefined, { onSuccess: () => { setExpired(false); setClicked(Date.now() - 2000) } })}>
                <Icon name="sync" size={18} /> {syncing ? (running && running.planned > 0 ? `Syncing ${running.processed} of ${running.planned}…` : 'Syncing…') : 'Sync now'}
              </button>
            </div>
          </section>
        )}
        {start.isError && (
          <p className="body-m" role="alert" style={{ color: 'var(--error)' }}>
            {start.error instanceof ApiError ? start.error.message : "Couldn't start the sync. Try again."}
          </p>
        )}

        <section aria-labelledby="mirrors-h" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <h2 id="mirrors-h" className="title-l">Mirrors</h2>
          <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
            {mirrors.map((a) => {
              const r = lastSummary[a.provider]
              const failed = failures(r)
              return (
                <article key={a.id} className="card-elev" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <ProviderChip id={a.provider} size={44} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <h3 className="title-m">{name(a.provider)}</h3>
                      <p className="body-m muted">{a.remote_username}</p>
                    </div>
                    {!a.sync_enabled ? <span className="tag tag-neutral">Paused</span>
                      : failed ? <span className="tag tag-err"><Icon name="alert" size={14} stroke={2.4} /> {failed} failed</span>
                      : <span className="tag tag-ok"><Icon name="check" size={14} stroke={2.6} /> In sync</span>}
                  </div>
                  <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8, margin: 0 }}>
                    <div><dt className="body-s muted">Last synced</dt><dd className="title-s" style={{ margin: 0 }}>{relative(a.last_synced_at)}</dd></div>
                    <div><dt className="body-s muted">Unmatched titles</dt><dd className="title-s" style={{ margin: 0 }}>{r?.unmapped ?? '–'}</dd></div>
                  </dl>
                  {!!r?.skipped && (
                    <p className="body-m muted" style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                      <Icon name="info" size={18} style={{ flex: 'none', marginTop: 1 }} />
                      <span>{r.skipped} {r.skipped === 1 ? 'title was' : 'titles were'} skipped: {r.skipped_reason}.</span>
                    </p>
                  )}
                  {a.needs_relink && <a href={`/auth/${a.provider}/login`} className="btn btn-tonal" style={{ alignSelf: 'flex-start' }}>Reconnect</a>}
                </article>
              )
            })}
            {mirrors.length === 0 && <p className="body-m muted">No mirrors yet. <Link to="/link">Link another list</Link>.</p>}
          </div>
        </section>

        <section aria-labelledby="activity-h" className="card-out" style={{ overflow: 'hidden' }}>
          <h2 id="activity-h" className="title-l" style={{ padding: '16px 16px 8px' }}>Recent activity</h2>
          <div className="list">
            {(runs.data ?? []).filter((r) => r.trigger !== 'preview').slice(0, 8).map((r) => {
              const interrupted = r.status === 'interrupted'
              const bad = !interrupted && (Object.values(r.summary).some((m) => failures(m) > 0) || r.status === 'failed')
              return (
                <div key={r.id} className="list-item">
                  <span className="avatar" style={{ background: 'var(--sc-high)', color: 'var(--on-surface-variant)' }}>
                    <Icon name={r.trigger === 'edit' ? 'edit' : 'sync'} size={20} />
                  </span>
                  <div className="list-text">
                    <span className="title-s">{TRIGGER[r.trigger] ?? r.trigger}</span>
                    <span className="body-m muted">{clock(r.started_at)} · {interrupted ? 'Stopped before it finished. Anything it didn\'t get to is retried on the next sync' : summarize(r, name)}</span>
                  </div>
                  <span className={`tag ${interrupted ? 'tag-warn' : bad ? 'tag-err' : r.finished_at ? 'tag-ok' : 'tag-info'}`}>
                    {interrupted ? 'Interrupted' : bad ? 'Errors' : r.finished_at ? 'Done' : r.planned > 0 ? `Running · ${r.processed} of ${r.planned}` : 'Running'}
                  </span>
                </div>
              )
            })}
            {runs.data && runs.data.filter((r) => r.trigger !== 'preview').length === 0 && (
              <p className="body-m muted" style={{ padding: 16 }}>Nothing yet. Your first sync will show up here.</p>
            )}
          </div>
        </section>
      </main>
    </>
  )
}
