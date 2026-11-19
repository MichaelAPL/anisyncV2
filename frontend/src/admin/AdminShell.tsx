import { Link, Navigate, NavLink, Outlet } from 'react-router'
import { RequireUser } from '../ui/Onboarding'
import { can, useAdmin } from './util'
import type { Me } from '../api/types'
import { ThemeButton } from '../ui/Chrome'
import { Icon } from '../ui/Icon'
import type { IconName } from '../ui/icons'
import { Avatar } from '../ui/Avatar'

const NAV: { to: string; icon: IconName; label: string; perm: string }[] = [
  { to: '/admin', icon: 'pulse', label: 'Overview', perm: 'stats.read' },
  { to: '/admin/syncs', icon: 'sync', label: 'Syncs', perm: 'syncs.read' },
  { to: '/admin/users', icon: 'users', label: 'Users', perm: 'users.read' },
  { to: '/admin/titles', icon: 'question', label: 'Titles', perm: 'titles.link' },
  { to: '/admin/providers', icon: 'server', label: 'Providers', perm: 'providers.manage' },
  { to: '/admin/site', icon: 'megaphone', label: 'Site', perm: 'site.manage' },
]


function Frame({ me }: { me: Me }) {
  if (me.permissions.length === 0) return <Navigate to="/dashboard" replace />
  const items = NAV.filter((n) => can(me, n.perm))
  const links = items.map((n) => (
    <NavLink key={n.to} to={n.to} end={n.to === '/admin'} className="nav-item"><span className="nav-ind"><Icon name={n.icon} /></span>{n.label}</NavLink>
  ))
  return (
    <div className="shell" style={{ flex: 1 }}>
      <nav className="rail" aria-label="Admin">
        <Link to="/admin" className="rail-logo" aria-label="Admin home"><Icon name="logo" size={26} stroke={2.2} /></Link>
        <span className="label-m" style={{ padding: '2px 8px', borderRadius: 8, background: 'var(--tertiary-container)', color: 'var(--on-tertiary-container)' }}>ADMIN</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>{links}</div>
        <Link to="/dashboard" className="nav-item" style={{ marginTop: 'auto' }}><span className="nav-ind"><Icon name="arrow-left" /></span>Back to app</Link>
      </nav>
      <div className="main">
        <Outlet context={me} />
        <nav className="navbar" aria-label="Admin (mobile)">{links.slice(0, 5)}</nav>
      </div>
    </div>
  )
}

/** Admin area: its own shell (admin rail), reachable only with at least one permission. */
export const AdminShell = () => <RequireUser>{(me) => <Frame me={me} />}</RequireUser>

export function AdminTop({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  const me = useAdmin()
  return (
    <header className="topbar">
      <span className="show-sm" style={{ marginRight: 8 }}>
        <Link to="/admin" className="rail-logo" style={{ width: 40, height: 40, borderRadius: 12 }} aria-label="Admin home"><Icon name="logo" size={20} stroke={2.2} /></Link>
      </span>
      <div style={{ flex: '1 1 auto', minWidth: 0 }}><h1 className="headline-s">{title}</h1>{sub && <p className="body-m muted">{sub}</p>}</div>
      <ThemeButton />
      {right}
      <Link to="/settings" className="icon-btn" aria-label="Your account" style={{ width: 44, height: 44 }}><Avatar me={me} /></Link>
    </header>
  )
}
