import type { ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

/** Themed button — replaces ad-hoc `.btn` usage and inline styles. */
function Button({ variant = 'secondary', size = 'md', className = '', type, ...rest }: Props) {
  const classes = ['btn', `btn--${variant}`, `btn--${size}`, className].filter(Boolean).join(' ')
  return <button type={type ?? 'button'} className={classes} {...rest} />
}

export default Button
