import { Link } from 'react-router'
import { useMe, useSaveProfile, useSite } from '../api/hooks'
import { Icon } from './Icon'
import { setTheme, isDark, useTheme } from '../theme'

export function Wordmark() {
  return (
    <Link to="/" className="wordmark">
      <span
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 40, height: 40,
          borderRadius: 12, background: 'var(--primary)', color: 'var(--on-primary)',
        }}
      >
        <Icon name="logo" size={22} stroke={2.2} />
      </span>
      AniSyncHub
    </Link>
  )
}

export function ThemeButton() {
  const theme = useTheme()
  const dark = isDark(theme)
  const me = useMe().data
  const save = useSaveProfile()
  return (
    <button
      type="button"
      className="icon-btn"
      aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      onClick={() => {
        const next = dark ? 'light' : 'dark'
        setTheme(next)
        if (me) save.mutate({ theme: next }) // signed in: remember it on the account too
      }}
    >
      <Icon name={dark ? 'sun' : 'moon'} />
    </button>
  )
}

export function Footer() {
  const { data: site } = useSite()
  return (
    <footer className="footer">
      <span>AniSyncHub is not affiliated with MyAnimeList, AniList or Kitsu.</span>
      <span>
        Anime IDs from <a href="https://github.com/cedya77/anime-offline-database">cedya77</a> and{' '}
        <a href="https://github.com/manami-project/anime-offline-database">manami-project</a> anime-offline-database (ODbL 1.0)
      </span>
      <Link to="/privacy">Privacy</Link>
      {site?.donation_url && (
        <a href={site.donation_url} target="_blank" rel="noopener noreferrer">
          Donate (one-off)
        </a>
      )}
    </footer>
  )
}
