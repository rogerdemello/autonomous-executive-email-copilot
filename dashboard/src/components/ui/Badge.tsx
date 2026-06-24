import type { Tone } from '../../labels'

interface Props {
  children: React.ReactNode
  tone?: Tone
  /** A small filled dot before the label — useful for risk/priority chips. */
  dot?: boolean
}

/** Semantic pill. Color comes from the tone token, never a raw hex. */
function Badge({ children, tone = 'neutral', dot = false }: Props) {
  return (
    <span className={`badge badge--${tone}`}>
      {dot && <span className="badge__dot" aria-hidden="true" />}
      {children}
    </span>
  )
}

export default Badge
