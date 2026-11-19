// Refreshes src/data/posters.json: the fixed set of popular titles shown on the signed-out landing page.
// Run: node scripts/update-posters.mjs   (uses AniList's public API, no key). Covers are hotlinked, never re-hosted.
import { writeFileSync } from 'node:fs'

const query = `query { Page(page: 1, perPage: 48) { media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {
  id title { romaji } coverImage { large color } } } }`
const res = await fetch('https://graphql.anilist.co', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ query }),
})
if (!res.ok) throw new Error(`AniList answered ${res.status}`)
const { data } = await res.json()
const posters = data.Page.media
  .filter((m) => m.coverImage?.large?.startsWith('https://s4.anilist.co/'))
  .map((m) => ({ id: m.id, title: m.title.romaji, cover: m.coverImage.large, color: m.coverImage.color ?? null }))
writeFileSync(new URL('../src/data/posters.json', import.meta.url), JSON.stringify(posters, null, 1) + '\n')
console.log(`wrote ${posters.length} posters`)
