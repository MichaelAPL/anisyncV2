import { useState } from 'react'
import { Link, NavLink, Outlet } from 'react-router'
import { useSite } from '../api/hooks'
import type { IconName } from './icons'
import type { Me } from '../api/types'
import { Avatar } from './Avatar'
import { ThemeButton } from './Chrome'
import { Footer } from './Chrome'
import { Icon } from './Icon'
import { RequireUser } from './Onboarding'

const NAV: { to: string; icon: IconName; label: string }[] = [
  { to: '/dashboard', icon: 'dashboard', label: 'Dashboard' },
  { to: '/list', icon: 'list', label: 'My list' },
  { to: '/settings', icon: 'settings', label: 'Settings' },
]

function NavItems({ admin }: { admin: boolean }) {
  const items = admin ? [...NAV, { to: '/admin', icon: 'shield' as IconName, label: 'Admin' }] : NAV
  return items.map((n) => (
    <NavLink key={n.to} to={n.to} className="nav-item">
      <span className="nav-ind"><Icon name={n.icon} /></span>
      {n.label}
    </NavLink>
  ))
}

const LEVEL: Record<string, string> = { info: 'banner-info', warning: 'banner-warn', error: 'banner-crit' }

/** Site announcement from the admins. Plain text only; dismissal is remembered per announcement. */
function SiteBanner() {
  const { data } = useSite()
  const key = data ? `anisync-banner-${data.updated_at}` : ''
  const [hidden, setHidden] = useState(() => {
    try { return !!key && localStorage.getItem(key) === '1' } catch { return false }
  })
  if (!data?.banner_text || hidden) return null
  return (
    <div className={`banner ${LEVEL[data.banner_level] ?? 'banner-info'}`} role="status">
      <Icon name="megaphone" size={20} style={{ flex: 'none' }} />
      <span className="body-m" style={{ flex: '1 1 260px' }}>{data.banner_text}</span>
      <button type="button" className="icon-btn" aria-label="Dismiss announcement" style={{ color: 'inherit' }}
        onClick={() => { try { localStorage.setItem(key, '1') } catch { /* ignore */ } setHidden(true) }}>
        <Icon name="close" size={20} />
      </button>
    </div>
  )
}

function Frame({ me }: { me: Me }) {
  const admin = me.permissions.length > 0
  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <Link to="/dashboard" className="rail-logo" aria-label="AniSync home"><Icon name="logo" size={26} stroke={2.2} /></Link>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}><NavItems admin={admin} /></div>
      </nav>
      <div className="main">
        <SiteBanner />
        <Outlet context={me} />
        <Footer />
        <nav className="navbar" aria-label="Main (mobile)"><NavItems admin={admin} /></nav>
      </div>
    </div>
  )
}

/** Signed-in layout: side rail on desktop, bottom bar on phones. */
export const AppShell = () => <RequireUser>{(me) => <Frame me={me} />}</RequireUser>

export function Topbar({ title, sub, right, me }: { title: string; sub?: string; right?: React.ReactNode; me: Me }) {
  return (
    <header className="topbar">
      <span className="show-sm" style={{ marginRight: 8 }}>
        <Link to="/dashboard" className="rail-logo" style={{ width: 40, height: 40, borderRadius: 12 }} aria-label="AniSync home">
          <Icon name="logo" size={20} stroke={2.2} />
        </Link>
      </span>
      <div style={{ flex: '1 1 auto', minWidth: 0 }}>
        <h1 className="headline-s">{title}</h1>
        {sub && <p className="body-m muted">{sub}</p>}
      </div>
      <ThemeButton />
      {right}
      <Link to="/settings" className="icon-btn" aria-label={`Account: ${me.display_name}`} style={{ width: 44, height: 44 }}>
        <Avatar me={me} />
      </Link>
    </header>
  )
}
