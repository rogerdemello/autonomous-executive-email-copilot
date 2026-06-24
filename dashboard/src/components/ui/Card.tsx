interface Props {
  children: React.ReactNode
  /** Optional heading rendered inside the card with consistent spacing. */
  title?: string
  /** Right-aligned controls beside the title. */
  actions?: React.ReactNode
  as?: 'div' | 'section'
  ariaLabel?: string
  className?: string
}

/** Surface container with a hairline border — the base of every panel. */
function Card({ children, title, actions, as = 'section', ariaLabel, className = '' }: Props) {
  const Tag = as
  return (
    <Tag className={`card ${className}`.trim()} aria-label={ariaLabel}>
      {(title || actions) && (
        <div className="card__head">
          {title && <h3 className="card__title">{title}</h3>}
          {actions && <div className="card__actions">{actions}</div>}
        </div>
      )}
      {children}
    </Tag>
  )
}

export default Card
