const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

/** "6 hours ago", "yesterday", "in 2 hours". */
export function relative(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const diff = new Date(iso).getTime() - Date.now()
  const abs = Math.abs(diff)
  const units: [Intl.RelativeTimeFormatUnit, number][] = [['day', 86_400_000], ['hour', 3_600_000], ['minute', 60_000]]
  for (const [unit, ms] of units) if (abs >= ms) return rtf.format(Math.round(diff / ms), unit)
  return 'just now'
}

export const clock = (iso: string) =>
  new Date(iso).toLocaleString('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })

export const utcHour = (iso: string) => new Date(iso).toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }) + ' UTC'

/** An unfinished run counts as running until it has gone 15 minutes without a sign of progress (its last heartbeat, or
 * its start), the same rule the server uses, so a crashed one can't block the UI forever but a long one isn't cut short. */
export function isRunning(run: { trigger: string; started_at: string; heartbeat_at?: string | null; finished_at: string | null }): boolean {
  const lastSign = run.heartbeat_at ?? run.started_at
  return run.trigger !== 'preview' && !run.finished_at && Date.now() - new Date(lastSign).getTime() < 15 * 60_000
}
