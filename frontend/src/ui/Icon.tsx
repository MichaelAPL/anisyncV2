import { ICONS, type IconName } from './icons'

/** Stroke icon from the design set. Decorative unless `label` is given. */
export function Icon({ name, size = 24, stroke = 2, label, style }: {
  name: IconName
  size?: number
  stroke?: number
  label?: string
  style?: React.CSSProperties
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={name === 'more' ? 3 : stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={style}
      dangerouslySetInnerHTML={{ __html: ICONS[name] }}
    />
  )
}
