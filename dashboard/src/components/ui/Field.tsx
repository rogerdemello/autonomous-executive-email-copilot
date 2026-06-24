import { useId } from 'react'

interface Props {
  label: string
  children: (id: string) => React.ReactNode
  /** Optional helper text under the control. */
  hint?: string
}

/**
 * Label + control wrapper that wires `htmlFor`/`id` for accessibility.
 * The render-prop hands the generated id to the input/select.
 */
function Field({ label, children, hint }: Props) {
  const id = useId()
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {hint && <p className="field__hint">{hint}</p>}
    </div>
  )
}

export default Field
