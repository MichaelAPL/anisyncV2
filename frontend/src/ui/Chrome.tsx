import { Link } from 'react-router'
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
      AniSync
    </Link>
  )
}

export function ThemeButton() {
  const theme = useTheme()
  const dark = isDark(theme)
  return (
    <button
      type="button"
      className="icon-btn"
      aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      onClick={() => setTheme(dark ? 'light' : 'dark')}
    >
      <Icon name={dark ? 'sun' : 'moon'} />
    </button>
  )
}

export function Footer() {
  return (
    <footer className="footer">
      <span>AniSync is not affiliated with MyAnimeList, AniList or Kitsu.</span>
      <span>
        Anime IDs from <a href="https://github.com/cedya77/anime-offline-database">cedya77</a> and{' '}
        <a href="https://github.com/manami-project/anime-offline-database">manami-project</a> anime-offline-database (ODbL 1.0)
      </span>
      <Link to="/privacy">Privacy</Link>
    </footer>
  )
}
