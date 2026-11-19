import { afterEach, describe, expect, it, vi } from 'vitest'
import { relative } from './format'
import { isDark, setTheme } from './theme'
import { safeImage } from './ui/images'

describe('safeImage', () => {
  it('accepts https images on a provider domain and its subdomains', () => {
    expect(safeImage('https://s4.anilist.co/file/cover.jpg')).toBe('https://s4.anilist.co/file/cover.jpg')
    expect(safeImage('https://cdn.myanimelist.net/images/a.jpg')).not.toBeNull()
    expect(safeImage('https://media.kitsu.app/anime/1/poster.jpg')).not.toBeNull()
  })
  it.each([
    ['plain http', 'http://s4.anilist.co/a.jpg'],
    ['another host', 'https://evil.example/a.jpg'],
    ['a look-alike host', 'https://notanilist.co/a.jpg'],
    ['a provider name inside another domain', 'https://anilist.co.evil.example/a.jpg'],
    ['a javascript url', 'javascript:alert(1)'],
    ['a data url', 'data:image/png;base64,AAAA'],
    ['garbage', 'not a url'],
    ['nothing', null],
  ])('rejects %s', (_label, url) => {
    expect(safeImage(url)).toBeNull()
  })
})

describe('relative', () => {
  afterEach(() => vi.useRealTimers())
  it('says never for nothing and just now for the moment', () => {
    expect(relative(null)).toBe('never')
    expect(relative(new Date().toISOString())).toBe('just now')
  })
  it('counts days, hours and minutes in the past and future', () => {
    const now = Date.now()
    expect(relative(new Date(now - 3 * 3_600_000).toISOString())).toBe('3 hours ago')
    expect(relative(new Date(now - 86_400_000).toISOString())).toBe('yesterday')
    expect(relative(new Date(now + 2 * 86_400_000 + 60_000).toISOString())).toBe('in 2 days')
  })
})

describe('theme', () => {
  it('applies the class, remembers the choice and forgets it for "system"', () => {
    setTheme('dark')
    expect(document.documentElement.className).toBe('as theme-dark')
    expect(localStorage.getItem('anisync-theme')).toBe('dark')
    expect(isDark('dark')).toBe(true)
    setTheme('system')
    expect(document.documentElement.className).toBe('as theme-auto')
    expect(localStorage.getItem('anisync-theme')).toBeNull()
    expect(isDark('light')).toBe(false)
  })
})
