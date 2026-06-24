type Kind = 'error' | 'success' | 'info'

interface Props {
  kind: Kind
  children: React.ReactNode
}

/** Inline status message — replaces the scattered inline-styled alert divs. */
function Banner({ kind, children }: Props) {
  const role = kind === 'error' ? 'alert' : 'status'
  return (
    <div className={`banner banner--${kind}`} role={role}>
      {children}
    </div>
  )
}

export default Banner
