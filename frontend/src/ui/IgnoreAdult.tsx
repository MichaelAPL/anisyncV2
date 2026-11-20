import { useSaveProfile } from '../api/hooks'
import type { Me } from '../api/types'

/** One switch for the whole master list: adult titles are not stored, shown or copied to any other list. */
export function IgnoreAdultSwitch({ me, name }: { me: Me; name: string }) {
  const save = useSaveProfile()
  return (
    <label className="toggle-row" style={{ justifyContent: 'space-between' }}>
      <span style={{ display: 'flex', flexDirection: 'column' }}>
        <span className="body-l">Ignore adult titles on my master list</span>
        <span className="body-m muted">
          Adult titles on {name} are not stored by AniSync, not shown in My list and not copied to any other list.
          Ones already on your other lists are never removed. Turn it off to bring them back on the next read.
        </span>
      </span>
      <input type="checkbox" role="switch" className="switch" checked={me.ignore_adult_titles} disabled={save.isPending}
        onChange={(e) => save.mutate({ ignore_adult_titles: e.target.checked })} />
    </label>
  )
}
