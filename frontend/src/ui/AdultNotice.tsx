import { useProviders, useRecheckAdult } from '../api/hooks'
import { ApiError } from '../api/client'
import type { Account } from '../api/types'
import { Icon } from './Icon'

/**
 * Tells the user that a linked account hides adult titles from AniSyncHub, and what to change in that provider's settings.
 * "I've changed it" looks at the account once: AniSyncHub doesn't poll for it.
 */
export function AdultNotice({ account, master }: { account: Account; master?: boolean }) {
  const { data } = useProviders()
  const recheck = useRecheckAdult()
  const provider = data?.find((p) => p.id === account.provider)
  if (!account.hides_adult || !provider) return null
  const failed = recheck.error instanceof ApiError ? recheck.error.message : recheck.isError ? "Couldn't reach AniSyncHub." : null
  return (
    <div className="banner banner-warn" role="note" style={{ borderRadius: 16, alignItems: 'flex-start', padding: '12px 16px' }}>
      <Icon name="info" size={20} style={{ flex: 'none', marginTop: 2 }} />
      <div style={{ flex: '1 1 260px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <p className="body-m">
          <strong>{provider.name} is hiding adult titles from AniSyncHub.</strong>{' '}
          {provider.adult_help ? `${provider.adult_help}, ` : ''}so adult titles on your list can sync.
          {master
            ? " Until then AniSyncHub can't see every title on this list, so it will never remove anything from your other lists because of it."
            : ' Until then they are skipped.'}
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button type="button" className="btn btn-tonal" disabled={recheck.isPending} onClick={() => recheck.mutate(account.id)}>
            {recheck.isPending ? 'Checking…' : "I've changed it, check again"}
          </button>
          {recheck.isSuccess && <span className="body-m" role="status">Still hidden. It can take a minute for {provider.name} to apply the change.</span>}
          {failed && <span className="body-m" role="alert">{failed}</span>}
        </div>
      </div>
    </div>
  )
}
