interface Props {
  title: string
  hint?: string
}

/** Calm, consistent placeholder for empty lists. */
function EmptyState({ title, hint }: Props) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {hint && <p className="empty__hint">{hint}</p>}
    </div>
  )
}

export default EmptyState
