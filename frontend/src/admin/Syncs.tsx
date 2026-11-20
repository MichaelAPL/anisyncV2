import { useState } from 'react'
import { Link } from 'react-router'
import { clock, relative } from '../format'
import { AdminTop } from './AdminShell'
import { can, level, useAdmin } from './util'
import { Card, Err, Loading, Stat } from './bits'
import { post, put, useAct, useAdminSite, useEvents, useJobs, useRunDetail, useRuns, useSyncStatus } from './hooks'

function RunRow({ r, open, onToggle }: { r: ReturnType<typeof useRuns>['data'] extends (infer T)[] | undefined ? T : never; open: boolean; onToggle: () => void }) {
  const detail = useRunDetail(open ? r.id : null)
  return (
    <>
      <button type="button" className="list-item list-link" onClick={onToggle} aria-expanded={open}>
        <div className="list-text">
          <span className="title-m">#{r.id} · {r.display_name ?? `user ${r.user_id}`} · {r.trigger}</span>
          <span className="body-m muted">{clock(r.started_at)} · {r.changes} changes · {r.mirrors} mirrors · {r.errors} errors · {r.warnings} warnings</span>
        </div>
        <span className={`tag ${level(r.errors ? 'error' : r.status)}`}>{r.errors ? 'Errors' : r.status}</span>
      </button>
      {open && (
        <div style={{ padding: '0 16px 16px 16px', background: 'var(--sc-low)' }}>
          <Loading q={detail} />
          {detail.data?.events.map((e) => (
            <p key={e.id} className="body-m" style={{ margin: '4px 0' }}>
              <span className={`tag ${level(e.level)}`} style={{ marginRight: 8 }}>{e.level}</span>{e.provider ? `${e.provider}: ` : ''}{e.message}
            </p>
          ))}
          {detail.data?.events.length === 0 && <p className="body-m muted">No events recorded for this run.</p>}
          <Link to={`/admin/users/${r.user_id}`} className="btn btn-text">Open user</Link>
        </div>
      )}
    </>
  )
}

export function Syncs() {
  const me = useAdmin()
  const status = useSyncStatus()
  const site = useAdminSite()
  const [runStatus, setRunStatus] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const runs = useRuns({ status: runStatus || undefined })
  const events = useEvents({ level: 'error' })
  const jobs = useJobs()
  const pause = useAct((paused: boolean) => put('/api/admin/syncs/scheduled', { paused }))
  const cancel = useAct((id: number) => post(`/api/admin/syncs/jobs/${id}/cancel`))
  const retry = useAct((id: number) => post(`/api/admin/syncs/jobs/${id}/retry`))
  const s = status.data
  return (
    <>
      <AdminTop title="Syncs and logs" />
      <main className="page" style={{ paddingTop: 0 }}>
        <Loading q={status} />
        {s && (
          <>
            {!s.queue_available && <div className="banner banner-warn" role="alert" style={{ borderRadius: 16, padding: '16px 20px' }}>The job queue isn't reachable.</div>}
            <div className="grid-stats" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
              <Stat n={s.stalled_jobs} label="stalled jobs" warn={s.stalled_jobs > 0} />
              <Stat n={s.full_reads_overdue} label="full reads overdue" warn={s.full_reads_overdue > 0} />
              <Stat n={s.users_in_next_batch} label={`users in the next batch (${relative(s.next_batch_at)})`} />
              <Stat n={s.last_batch ? `${s.last_batch.ok}/${s.last_batch.runs}` : '–'} label="runs OK in the last batch" warn={!!s.last_batch?.error} />
            </div>
            <Card id="q-h" title="Queues">
              <div className="list">{s.queues.map((q) => <div key={q.queue} className="list-item"><span className="title-m" style={{ flex: 1 }}>{q.queue}</span><span className="body-m muted">{q.todo} waiting · {q.doing} running · {q.failed} failed</span></div>)}</div>
            </Card>
          </>
        )}
        {can(me, 'syncs.control') && site.data && (
          <Card id="sched-h" title="Scheduled syncs" sub="Pausing stops the hourly batches. Edits made in AniSync still sync.">
            <label className="toggle-row" style={{ justifyContent: 'space-between', padding: 16 }}>
              <span className="body-l">Pause scheduled syncs</span>
              <input type="checkbox" role="switch" className="switch" checked={site.data.scheduled_syncs_paused} disabled={pause.isPending} onChange={(e) => pause.mutate(e.target.checked)} />
            </label>
            <Err error={pause.error} />
          </Card>
        )}
        <Card id="runs-h" title="Sync runs" right={
          <div className="field" style={{ width: 180 }}><label htmlFor="rs">Status</label>
            <select id="rs" value={runStatus} onChange={(e) => setRunStatus(e.target.value)}><option value="">All</option><option value="ok">OK</option><option value="error">Errors</option></select></div>}>
          <Loading q={runs} />
          <div className="list">{runs.data?.map((r) => <RunRow key={r.id} r={r} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)} />)}</div>
          {runs.data?.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>No runs match.</p>}
        </Card>
        <Card id="ev-h" title="Recent errors">
          <Loading q={events} />
          <div className="list">{events.data?.map((e) => (
            <div key={e.id} className="list-item"><div className="list-text"><span className="title-s">{e.display_name ?? 'unknown user'} · {e.provider ?? '–'} · {e.kind}</span><span className="body-m muted">{clock(e.created_at)} · {e.message}</span></div></div>
          ))}</div>
          {events.data?.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>No errors recorded.</p>}
        </Card>
        <Card id="jobs-h" title="Jobs">
          <Loading q={jobs} />
          <div className="list">{jobs.data?.map((j) => (
            <div key={j.id} className="list-item" style={{ flexWrap: 'wrap' }}>
              <div className="list-text" style={{ flex: '1 1 240px' }}><span className="title-s">#{j.id} {j.task.split('.').pop()} · {j.queue}</span>
                <span className="body-m muted">{j.status} · attempts {j.attempts}{j.user_id ? ` · user ${j.user_id}` : ''}{j.scheduled_at ? ` · ${clock(j.scheduled_at)}` : ''}</span></div>
              {can(me, 'syncs.retry') && (j.status === 'todo' || j.status === 'doing') && <button type="button" className="btn btn-text" onClick={() => cancel.mutate(j.id)}>Cancel</button>}
              {can(me, 'syncs.retry') && j.status === 'failed' && <button type="button" className="btn btn-text" onClick={() => retry.mutate(j.id)}>Retry</button>}
            </div>
          ))}</div>
          <Err error={cancel.error ?? retry.error} />
        </Card>
      </main>
    </>
  )
}
