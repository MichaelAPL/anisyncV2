import { useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router'
import { ApiError } from '../api/client'
import { useList, useProviders, useRefreshList, useRemoveEntry, useSaveEntry } from '../api/hooks'
import type { EntryInput, ListEntry, Me, Status } from '../api/types'
import { Cover } from '../ui/Cover'
import { Icon } from '../ui/Icon'
import { Topbar } from '../ui/Shell'
import { useIsPhone } from '../ui/useMedia'

const STATUSES: [Status, string][] = [
  ['watching', 'Watching'], ['completed', 'Completed'], ['on_hold', 'On hold'], ['dropped', 'Dropped'], ['planning', 'Planning'],
]
const LABEL = Object.fromEntries(STATUSES) as Record<Status, string>
const PAGE = 100

const toInput = (e: ListEntry): EntryInput => ({
  status: e.status, progress: e.progress, score: e.score, rewatching: e.rewatching, started_at: e.started_at, finished_at: e.finished_at,
})

function detail(e: ListEntry): string {
  const bits: string[] = [LABEL[e.status]]
  if (e.rewatching) bits.push('Rewatching')
  if (e.status === 'planning') return bits.join(' · ')
  bits.push(e.total_episodes ? `${e.progress} of ${e.total_episodes} episodes` : `${e.progress} episodes`)
  bits.push(e.score ? `Score ${e.score / 10}` : 'No score')
  return bits.join(' · ')
}

function EntryForm({ entry, masterName, onClose, onSaved, onRemoved }: {
  entry: ListEntry
  masterName: string
  onClose: () => void
  onSaved: (previous: EntryInput, queued: number) => void
  onRemoved: () => void
}) {
  const save = useSaveEntry()
  const remove = useRemoveEntry()
  const [f, setF] = useState<EntryInput>(toInput(entry))
  const [score, setScore] = useState(entry.score ? String(entry.score / 10) : '')
  const [confirming, setConfirming] = useState(false)
  const max = entry.total_episodes ?? undefined
  const set = <K extends keyof EntryInput>(k: K, v: EntryInput[K]) => setF((p) => ({ ...p, [k]: v }))
  const err = (save.error ?? remove.error) instanceof ApiError ? (save.error ?? remove.error)!.message : save.isError || remove.isError ? "Couldn't reach AniSync." : null
  const num = score === '' ? null : Math.round(Number(score) * 10)
  const bad = num !== null && (Number.isNaN(num) || num < 0 || num > 100)

  return (
    <form style={{ display: 'flex', flexDirection: 'column', gap: 20 }} onSubmit={(ev) => {
      ev.preventDefault()
      if (bad) return
      save.mutate({ mediaId: entry.media_id, entry: { ...f, score: num || null } }, { onSuccess: (r) => onSaved(toInput(entry), r.queued_mirrors) })
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <Cover title={entry.title} url={entry.image_url} w={56} h={80} />
        <div style={{ flex: 1, minWidth: 0 }}><p className="label-m muted">EDITING</p><h2 id="sheet-h" className="title-l">{entry.title}</h2></div>
        <button type="button" className="icon-btn" aria-label="Close editor" onClick={onClose}><Icon name="close" size={20} /></button>
      </div>
      <div className="field">
        <label htmlFor="status">Status</label>
        <select id="status" value={f.status} onChange={(e) => set('status', e.target.value as Status)}>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button type="button" className="icon-btn" aria-label="One episode fewer" style={{ border: '1px solid var(--outline)' }}
          onClick={() => set('progress', Math.max(0, f.progress - 1))}><Icon name="minus" size={20} /></button>
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="eps">Episodes watched</label>
          <input id="eps" type="number" inputMode="numeric" min={0} max={max} value={f.progress}
            onChange={(e) => set('progress', Math.max(0, Math.floor(Number(e.target.value) || 0)))} />
          {max && <span className="support">of {max}</span>}
        </div>
        <button type="button" className="icon-btn" aria-label="One episode more" style={{ border: '1px solid var(--outline)' }}
          onClick={() => set('progress', max ? Math.min(max, f.progress + 1) : f.progress + 1)}><Icon name="plus" size={20} /></button>
      </div>
      <div className="field">
        <label htmlFor="score">Score</label>
        <input id="score" type="number" step={0.5} min={0} max={10} value={score} onChange={(e) => setScore(e.target.value)} aria-invalid={bad} />
        <span className="support">{bad ? 'Use a number from 0 to 10' : 'Providers that keep whole numbers round it'}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
        <div className="field"><label htmlFor="start">Started</label><input id="start" type="date" value={f.started_at ?? ''} onChange={(e) => set('started_at', e.target.value || null)} /></div>
        <div className="field"><label htmlFor="finish">Finished</label><input id="finish" type="date" value={f.finished_at ?? ''} onChange={(e) => set('finished_at', e.target.value || null)} /></div>
      </div>
      <label className="toggle-row body-l" style={{ justifyContent: 'space-between' }}>
        Rewatching <input type="checkbox" role="switch" className="switch" checked={f.rewatching} onChange={(e) => set('rewatching', e.target.checked)} />
      </label>
      <p className="body-s muted" style={{ display: 'flex', gap: 8 }}><Icon name="sync" size={16} style={{ flex: 'none' }} /> Saves to {masterName} now, then updates your mirrors.</p>
      {err && <p className="body-m" role="alert" style={{ color: 'var(--error)' }}>{err}</p>}
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {confirming ? (
          <span style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <span className="body-m">Remove from your list?</span>
            <button type="button" className="btn btn-text" style={{ color: 'var(--error)' }} disabled={remove.isPending}
              onClick={() => remove.mutate(entry.media_id, { onSuccess: onRemoved })}>Yes, remove</button>
            <button type="button" className="btn btn-text" onClick={() => setConfirming(false)}>Cancel</button>
          </span>
        ) : (
          <button type="button" className="btn btn-text" style={{ color: 'var(--error)' }} onClick={() => setConfirming(true)}><Icon name="trash" size={18} /> Remove</button>
        )}
        <button type="submit" className="btn btn-filled" disabled={save.isPending || bad}>{save.isPending ? 'Saving…' : 'Save'}</button>
      </div>
    </form>
  )
}

export function ListEditor() {
  const me = useOutletContext<Me>()
  const providers = useProviders()
  const list = useList()
  const refresh = useRefreshList()
  const save = useSaveEntry()
  const phone = useIsPhone()
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState<Status | 'all'>('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [shown, setShown] = useState(PAGE)
  const [toast, setToast] = useState<{ text: string; undo?: { mediaId: string; entry: EntryInput } } | null>(null)

  const master = me.accounts.find((a) => a.is_master)
  const masterName = providers.data?.find((p) => p.id === master?.provider)?.name ?? 'your master list'
  const entries = useMemo(() => list.data ?? [], [list.data])
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: entries.length }
    for (const e of entries) c[e.status] = (c[e.status] ?? 0) + 1
    return c
  }, [entries])
  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return entries.filter((e) => (filter === 'all' || e.status === filter) && (!needle || e.title.toLowerCase().includes(needle)))
  }, [entries, q, filter])
  const current = entries.find((e) => e.media_id === selected) ?? null

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 8000)
    return () => clearTimeout(t)
  }, [toast])

  const form = current && (
    <EntryForm key={current.media_id + (current.updated_at ?? '')} entry={current} masterName={masterName} onClose={() => setSelected(null)}
      onSaved={(previous, queued) => {
        setToast({ text: queued > 0 ? `Saved to ${masterName}. Updating ${queued} mirror${queued === 1 ? '' : 's'}…` : `Saved to ${masterName}.`, undo: { mediaId: current.media_id, entry: previous } })
        setSelected(null)
      }}
      onRemoved={() => { setToast({ text: `Removed from ${masterName}.` }); setSelected(null) }} />
  )

  const sub = `${masterName} · ${entries.length} entries. Edits go to ${masterName} first, then to your mirrors.`
  return (
    <>
      <Topbar title="My list" sub={list.data ? sub : undefined} me={me} right={
        <button type="button" className="btn btn-text" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
          <Icon name="retry" size={18} /> {refresh.isPending ? 'Checking…' : `Check ${masterName}`}
        </button>} />
      <main className="page" style={{ paddingTop: 0, gap: 16 }}>
        {list.isPending && <p className="body-l muted" role="status">Loading your list… the first read of a long list can take a minute.</p>}
        {list.isError && <p className="body-l" role="alert" style={{ color: 'var(--error)' }}>{list.error instanceof ApiError ? list.error.message : "Couldn't load your list."}</p>}
        {refresh.data && <p className="body-m muted" role="status">{refresh.data.changed ? `${refresh.data.changed} changes found on ${masterName}.` : `No new changes on ${masterName}.`}</p>}
        {list.data && (
          <>
            <div className="search" role="search"><Icon name="search" /><input type="search" aria-label="Search your list" placeholder="Search your list" value={q} onChange={(e) => { setQ(e.target.value); setShown(PAGE) }} /></div>
            <div className="chip-row" role="group" aria-label="Filter by status">
              {([['all', 'All'], ...STATUSES] as [Status | 'all', string][]).map(([v, l]) => (
                <button key={v} type="button" className={`chip${filter === v ? ' chip-on' : ''}`} aria-pressed={filter === v} onClick={() => { setFilter(v); setShown(PAGE) }}>
                  {filter === v && <Icon name="check" size={18} />} {l} {counts[v] ?? 0}
                </button>
              ))}
            </div>
            <div className="split">
              <div className="split-main" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <section className="card-out list" aria-label="Entries" style={{ overflow: 'hidden' }}>
                  {visible.slice(0, shown).map((e) => (
                    <button key={e.media_id} type="button" className={`list-item list-link${e.media_id === selected ? ' list-selected' : ''}`}
                      aria-current={e.media_id === selected || undefined} onClick={() => setSelected(e.media_id)} style={{ width: '100%', textAlign: 'left', font: 'inherit', border: 'none' }}>
                      <Cover title={e.title} url={e.image_url} />
                      <span className="list-text"><span className="title-m">{e.title}</span><span className="body-m muted">{detail(e)}</span></span>
                      <Icon name="chevron-right" size={20} />
                    </button>
                  ))}
                  {visible.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>{entries.length === 0 ? `Your ${masterName} list is empty.` : 'Nothing matches.'}</p>}
                </section>
                {visible.length > shown && <button type="button" className="btn btn-tonal" style={{ alignSelf: 'center' }} onClick={() => setShown(shown + PAGE)}>Show more ({visible.length - shown} left)</button>}
              </div>
              {!phone && current && <aside className="split-side card" aria-labelledby="sheet-h" style={{ padding: '20px 20px 24px' }}>{form}</aside>}
            </div>
          </>
        )}
        {toast && (
          <div className="snackbar" role="status" style={{ position: 'sticky', bottom: 96 }}>
            <span style={{ flex: 1 }}>{toast.text}</span>
            {toast.undo && <button type="button" className="btn btn-text" disabled={save.isPending}
              onClick={() => { save.mutate({ mediaId: toast.undo!.mediaId, entry: toast.undo!.entry }); setToast(null) }}>Undo</button>}
          </div>
        )}
      </main>
      {phone && current && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 20 }}>
          <div style={{ position: 'absolute', inset: 0, background: 'var(--scrim)' }} onClick={() => setSelected(null)} />
          <section role="dialog" aria-modal="true" aria-labelledby="sheet-h" style={{
            position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '92vh', overflowY: 'auto', ['--field-bg' as string]: 'var(--sc-low)',
            background: 'var(--sc-low)', borderRadius: '28px 28px 0 0', padding: '8px 20px 24px',
          }}>
            <span aria-hidden="true" style={{ display: 'block', margin: '14px auto 18px', width: 32, height: 4, borderRadius: 2, background: 'var(--outline)' }} />
            {form}
          </section>
        </div>
      )}
    </>
  )
}
