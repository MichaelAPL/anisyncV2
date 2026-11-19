// Setup (link lists, choose master, first sync) is finished once the user applies a sync or skips it.
const KEY = 'anisync-setup-done'

export function setupDone(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function finishSetup() {
  try {
    localStorage.setItem(KEY, '1')
  } catch {
    /* storage blocked: the user can still reach the dashboard from the link page */
  }
}
