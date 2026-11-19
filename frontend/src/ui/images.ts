// Covers and avatars are hotlinked from the providers, never re-hosted. Only https images on a provider's own
// domain are used (mirrors `image_hosts` in the backend); anything else falls back to the placeholder tile.
const HOSTS = ['myanimelist.net', 'anilist.co', 'kitsu.app', 'kitsu.io']

export function safeImage(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const u = new URL(url)
    if (u.protocol !== 'https:') return null
    return HOSTS.some((h) => u.hostname === h || u.hostname.endsWith('.' + h)) ? u.href : null
  } catch {
    return null
  }
}
