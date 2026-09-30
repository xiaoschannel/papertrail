import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'

export interface Choice {
  value: string
  label: string
  /** Shown muted after the label in the list; not searched. */
  detail?: ReactNode
  /** What typing is matched against; the label unless given. */
  search?: string
}

/** Rows drawn at once: past this, typing narrows the list rather than scrolling it. */
const SHOWN = 200
/** The list's tallest (rem), as .search-select__list's max-height in styles.css. */
const TALLEST = 20
/** Room (px) kept between the list and the window's edge. */
const EDGE = 8

// Width and case both fold, so full-width Ｓｔａｒ or katakana typed through an IME finds half-width names too.
const fold = (text: string) => text.normalize('NFKC').toLowerCase()

/**
 * The choices containing every typed word, those starting with the query first, each group in the given
 * order. `folded` is each choice's searched text, folded once per list rather than once per keystroke.
 */
function narrow(choices: Choice[], folded: string[], query: string): Choice[] {
  const needle = fold(query.trim())
  if (!needle) return choices
  const words = needle.split(/\s+/)
  const leading: Choice[] = []
  const rest: Choice[] = []
  choices.forEach((choice, i) => {
    const hay = folded[i] ?? ''
    if (!words.every((w) => hay.includes(w))) return
    if (hay.startsWith(needle)) leading.push(choice)
    else rest.push(choice)
  })
  return [...leading, ...rest]
}

/**
 * A dropdown you can type into: click to see every choice, type to narrow them (any part of the name, in
 * any case or width), arrows and Enter or a click to pick, Escape to leave it as it was. For a list too
 * long to scroll through — merchants, brands, documents.
 *
 * Focused, the field is empty with the current choice as its placeholder, so typing starts a search
 * rather than editing the name. The list floats over the page, so opening it moves nothing.
 */
export function SearchSelect({ id, choices, value, onChange, placeholder, disabled = false, className = '' }: {
  id?: string
  choices: Choice[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  className?: string
}) {
  const listId = useId()
  const input = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const [focused, setFocused] = useState(false)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)

  const folded = useMemo(() => choices.map((c) => fold(c.search ?? c.label)), [choices])
  const shown = useMemo(() => narrow(choices, folded, query), [choices, folded, query])
  const selected = choices.find((c) => c.value === value)
  const selectedLabel = selected?.label ?? value
  // past the cap only as far as the current choice, so opening always shows where you are
  const drawn = shown.slice(0, Math.max(SHOWN, selected ? shown.indexOf(selected) + 1 : 0))

  const show = () => {
    setActive(Math.max(0, choices.findIndex((c) => c.value === value)))
    setOpen(true)
  }
  const close = () => {
    setOpen(false)
    setQuery('')
  }
  const pick = (choice: Choice | undefined) => {
    if (!choice) return
    close()
    if (choice.value !== value) onChange(choice.value)
    input.current?.blur()   // so the page's own shortcut keys work again straight away
  }

  // keep the highlighted row in view as the arrows move it
  useEffect(() => {
    if (open) list.current?.children[active]?.scrollIntoView({ block: 'nearest' })
  }, [open, active])

  // As a select does: open downward unless the list won't fit there and there's more room above, and
  // never past the window's edge. Checked again as the list grows, but once it has opened upward it stays
  // so until it closes: a list that shrinks as you type doesn't jump back down.
  const [place, setPlace] = useState<{ up: boolean; room: number } | null>(null)
  useLayoutEffect(() => {
    if (!open || !input.current || !list.current) return setPlace(null)
    const field = input.current.getBoundingClientRect()
    const below = window.innerHeight - field.bottom - EDGE
    const above = field.top - EDGE
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize)
    const wanted = Math.min(list.current.scrollHeight + 2, TALLEST * rem)   // + its border
    const up = Boolean(place?.up) || (wanted > below && above > below)
    const room = up ? above : below
    if (place?.up !== up || place.room !== room) setPlace({ up, room })
  }, [open, drawn.length])

  return (
    <div className={`search-select ${className}`.trim()}>
      <input ref={input} id={id} type="text" role="combobox" autoComplete="off" spellCheck={false}
        aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={open && drawn[active] ? `${listId}-${active}` : undefined}
        disabled={disabled}
        value={focused ? query : selectedLabel}
        placeholder={focused ? selectedLabel || placeholder : placeholder}
        onFocus={() => setFocused(true)}
        onClick={() => { if (!open) show() }}
        onBlur={() => {
          setFocused(false)
          close()
        }}
        onChange={(e) => {
          setQuery(e.target.value)
          setActive(0)
          setOpen(true)
        }}
        onKeyDown={(e) => {
          // Enter and the arrows also confirm and choose an IME's conversion; those keys are the IME's
          if (e.nativeEvent.isComposing) return
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            if (!open) return show()
            const step = e.key === 'ArrowDown' ? 1 : -1
            setActive((i) => Math.max(0, Math.min(i + step, drawn.length - 1)))
          } else if (e.key === 'Enter' && open) {
            e.preventDefault()
            pick(drawn[active])
          } else if (e.key === 'Escape' && open) {
            e.preventDefault()
            close()
          }
        }} />
      {open && (
        <ul ref={list} id={listId} role="listbox" className={`search-select__list${place?.up ? ' up' : ''}`}
          style={place ? { maxHeight: `min(${TALLEST}rem, ${place.room}px)` } : undefined}
          // Pressing anywhere in the list (its scrollbar, a hint row) mustn't take focus from the field,
          // which would close the list under the pointer. A row is picked on mousedown for the same reason.
          onMouseDown={(e) => e.preventDefault()}>
          {drawn.length === 0 && <li className="search-select__none">No match.</li>}
          {drawn.map((choice, i) => (
            <li key={choice.value} id={`${listId}-${i}`} role="option" aria-selected={i === active}
              className={`search-select__option${i === active ? ' active' : ''}${choice.value === value ? ' current' : ''}`}
              onMouseDown={() => pick(choice)}
              onMouseMove={() => setActive(i)}>
              <span className="search-select__label">{choice.label}</span>
              {choice.detail != null && <span className="search-select__detail">{choice.detail}</span>}
            </li>
          ))}
          {shown.length > drawn.length && (
            <li className="search-select__none">{shown.length - drawn.length} more — type to narrow</li>
          )}
        </ul>
      )}
    </div>
  )
}
