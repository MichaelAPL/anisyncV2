import { useProviders } from '../api/hooks'
import type { Account } from '../api/types'
import { Icon } from './Icon'

/** Tells the user that a linked account hides adult titles from AniSync, and what to change in that provider's settings. */
export function AdultNotice({ account, master }: { account: Account; master?: boolean }) {
  const { data } = useProviders()
  const provider = data?.find((p) => p.id === account.provider)
  if (!account.hides_adult || !provider) return null
  return (
    <div className="banner banner-warn" role="note" style={{ borderRadius: 16, alignItems: 'flex-start', padding: '12px 16px' }}>
      <Icon name="info" size={20} style={{ flex: 'none', marginTop: 2 }} />
      <p className="body-m" style={{ flex: '1 1 260px' }}>
        <strong>{provider.name} is hiding adult titles from AniSync.</strong>{' '}
        {provider.adult_help ? `${provider.adult_help}, ` : ''}so adult titles on your list can sync.
        {master
          ? ' Until then AniSync can\'t see every title on this list, so it will never remove anything from your other lists because of it.'
          : ' Until then they are skipped.'}
      </p>
    </div>
  )
}
