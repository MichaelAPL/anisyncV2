import { Link } from 'react-router'
import { relative } from '../format'
import { AdminTop } from './AdminShell'
import { useProviderName } from './util'
import { Card, Loading, Stat } from './bits'
import { useDaily, useExpiring, useOverview } from './hooks'

export function Overview() {
  const o = useOverview()
  const d = useDaily(14)
  const exp = useExpiring()
  const name = useProviderName()
  return (
    <>
      <AdminTop title="Overview" />
      <main className="page" style={{ paddingTop: 0 }}>
        <Loading q={o} />
        {o.data && (
          <div className="grid-stats" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
            <Stat n={o.data.users_total} label="users" />
            <Stat n={o.data.users_with_master} label="with a master list" />
            <Stat n={o.data.new_today} label="new today" />
            <Stat n={o.data.new_7d} label="new in 7 days" />
            <Stat n={o.data.active_7d} label="active in 7 days" />
            <Stat n={o.data.active_30d} label="active in 30 days" />
            <Stat n={o.data.banned} label="banned" warn={o.data.banned > 0} />
          </div>
        )}
        {o.data && (
          <Card id="prov-h" title="Linked accounts per provider">
            <div className="list">
              {o.data.providers.map((p) => (
                <div key={p.provider} className="list-item">
                  <div className="list-text"><span className="title-m">{name(p.provider)}</span>
                    <span className="body-m muted">{p.accounts} accounts · {p.masters} masters · {p.needs_relink} need relinking · {p.expiring_soon} expiring soon</span></div>
                </div>
              ))}
            </div>
          </Card>
        )}
        <Card id="days-h" title="Last 14 days" sub="Sync runs, changes written and new users per day.">
          <Loading q={d} />
          {d.data && (
            <div style={{ overflowX: 'auto', padding: '0 16px 16px' }}>
              <table className="body-m" style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead><tr className="label-m muted" style={{ textAlign: 'left' }}><th>Day</th><th>New users</th><th>Runs OK</th><th>Runs failed</th><th>Changes</th></tr></thead>
                <tbody>
                  {[...d.data.days].reverse().map((r) => (
                    <tr key={r.day} style={{ borderTop: '1px solid var(--outline-variant)' }}>
                      <td style={{ padding: '6px 0' }}>{r.day}</td><td className="num">{r.new_users}</td><td className="num">{r.runs_ok}</td>
                      <td className="num" style={r.runs_error ? { color: 'var(--error)' } : undefined}>{r.runs_error}</td><td className="num">{r.changes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card id="exp-h" title="Connections ending soon" sub="Accounts that need to be reconnected within 14 days (or already did).">
          <Loading q={exp} />
          <div className="list">
            {exp.data?.map((a) => (
              <Link key={a.account_id} to={`/admin/users/${a.user_id}`} className="list-item list-link">
                <div className="list-text"><span className="title-m">{a.display_name} · {name(a.provider)}</span>
                  <span className="body-m muted">{a.remote_username}{a.is_master ? ' (master)' : ''} · {a.state === 'expired' ? 'expired' : `expires ${relative(a.expires_at)}`}</span></div>
              </Link>
            ))}
            {exp.data?.length === 0 && <p className="body-m muted" style={{ padding: 16 }}>Nothing is about to expire.</p>}
          </div>
        </Card>
      </main>
    </>
  )
}
