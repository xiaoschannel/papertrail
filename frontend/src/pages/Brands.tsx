import { useState, type MouseEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type SuggestionSettings } from '../api/client.ts'
import { useConfig } from '../api/config.ts'
import type { Brand } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import { num } from '../format.ts'
import './curate.css'

type Section = 'overview' | 'suggestions' | 'manage'

/**
 * The brand registry decides how merchant names are grouped in every chart: a brand is a label plus
 * prefixes, a receipt matches by longest prefix, and what's left of the name is its branch location.
 */
export default function Brands() {
  const [section, setSection] = useState<Section>('overview')
  const brands = useQuery({ queryKey: ['brands'], queryFn: api.brands.list })

  if (brands.isPending) return <Loading what="brands" />
  if (brands.error) return <ErrorState error={brands.error} />
  const { overview, unmatched } = brands.data

  return (
    <div className="curate-page brands-page">
      <h1>Brand registry</h1>
      <p className="page-sub">Group merchant names that belong to the same brand, and split off the branch.</p>

      <div className="controls">
        <div className="field">
          <label>Section</label>
          <div className="segmented">
            {([['overview', 'Overview'], ['suggestions', 'Suggestions'], ['manage', 'Manage brands']] as const)
              .map(([value, label]) => (
                <button key={value} className={section === value ? 'on' : ''}
                  onClick={() => setSection(value)}>{label}</button>
              ))}
          </div>
        </div>
      </div>

      <div className="tiles">
        <Tile label="Receipts" value={num(overview.receipts)} />
        <Tile label="Grouped" value={num(overview.matched)} />
        <Tile label="Ungrouped" value={num(overview.unmatched)} />
        <Tile label="Brands" value={num(brands.data.brands.length)} />
      </div>

      {section === 'overview' && <UnmatchedNames rows={unmatched} />}
      {section === 'suggestions' && <Suggestions brands={brands.data.brands} unmatched={unmatched} />}
      {section === 'manage' && <ManageBrands brands={brands.data.brands} />}
    </div>
  )
}

function UnmatchedNames({ rows }: { rows: { name: string; count: number }[] }) {
  const [alphabetical, setAlphabetical] = useState(false)
  const sorted = alphabetical
    ? [...rows].sort((a, b) => a.name.localeCompare(b.name))
    : rows
  return (
    <Card title="Names no brand matches" hint={`${rows.length} name(s)`} className="card--uncapped">
      {rows.length === 0 ? <Empty>Every receipt name matches a brand.</Empty> : (
        <>
          <div className="segmented">
            <button className={alphabetical ? '' : 'on'} onClick={() => setAlphabetical(false)}>By frequency</button>
            <button className={alphabetical ? 'on' : ''} onClick={() => setAlphabetical(true)}>Alphabetical</button>
          </div>
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Name</th><th className="num">Receipts</th></tr></thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={row.name}>
                    <td className="ellipsis" title={row.name}>{row.name}</td>
                    <td className="num">{num(row.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  )
}

function Suggestions({ brands, unmatched }: { brands: Brand[]; unmatched: { name: string; count: number }[] }) {
  const queryClient = useQueryClient()
  const config = useConfig()
  // Receipts per unmatched name, folded the way the suggestions are matched, so a name spelled two
  // ways counts once. It turns "3 names" into "3 names, 14 receipts" — which is what tells you
  // whether a prefix is worth a brand.
  const receiptsPerName = new Map<string, number>()
  for (const row of unmatched) {
    const key = row.name.trim().toLowerCase()
    receiptsPerName.set(key, (receiptsPerName.get(key) ?? 0) + row.count)
  }
  const receiptsOf = (name: string) => receiptsPerName.get(name.trim().toLowerCase()) ?? 0
  const [settings, setSettings] = useState<SuggestionSettings | null>(null)
  const [target, setTarget] = useState('')
  const current: SuggestionSettings = settings ?? {
    boundaryOnly: config.data?.prefix_suggestion_boundary_only ?? true,
    maxLength: config.data?.prefix_suggestion_max_length ?? 24,
    minLength: config.data?.prefix_suggestion_min_length ?? 3,
    minCount: config.data?.prefix_suggestion_min_count ?? 2,
  }
  const suggestions = useQuery({
    queryKey: ['brands', 'suggestions', current],
    queryFn: () => api.brands.suggestions(current),
    enabled: config.isSuccess,
    placeholderData: (previous) => previous,
  })
  const done = () => queryClient.invalidateQueries({ queryKey: ['brands'] })
  // Suggestions are casefolded for matching; the label is what you read in the charts, so title it
  // (as Streamlit did) and keep the prefix exactly as suggested.
  const titled = (prefix: string) => prefix.replace(/(^|\s)(\p{L})/gu, (_m, gap, letter) => gap + letter.toUpperCase())
  const create = useMutation({ mutationFn: (prefix: string) =>
    api.brands.create({ label: titled(prefix), prefixes: [prefix] }), onSuccess: done })
  const add = useMutation({ mutationFn: (prefix: string) => api.brands.addPrefix(target, prefix), onSuccess: done })
  const set = <K extends keyof SuggestionSettings>(key: K, value: SuggestionSettings[K]) =>
    setSettings({ ...current, [key]: value })

  return (
    <Card title="Suggested prefixes" hint="from the names above" className="card--uncapped">
      <div className="controls">
        <label className="config-check">
          <input type="checkbox" checked={current.boundaryOnly}
            onChange={(e) => set('boundaryOnly', e.target.checked)} />
          Only cut at word boundaries
        </label>
        <div className="field">
          <label htmlFor="sg-min-count">Seen at least</label>
          <input id="sg-min-count" className="limit-input" type="number" min={1} max={50} value={current.minCount}
            onChange={(e) => set('minCount', Number(e.target.value) || 1)} />
        </div>
        <div className="field">
          <label htmlFor="sg-min-len">Min length</label>
          <input id="sg-min-len" className="limit-input" type="number" min={1} max={24} value={current.minLength}
            onChange={(e) => set('minLength', Number(e.target.value) || 1)} />
        </div>
        <div className="field">
          <label htmlFor="sg-max-len">Max length</label>
          <input id="sg-max-len" className="limit-input" type="number" min={4} max={80} value={current.maxLength}
            onChange={(e) => set('maxLength', Number(e.target.value) || 4)} />
        </div>
        <div className="field">
          <label htmlFor="sg-target">Add to brand</label>
          <select id="sg-target" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">(pick a brand)</option>
            {brands.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        </div>
      </div>

      {(create.error || add.error) && (
        <div className="error-banner" role="alert">{(create.error ?? add.error)?.message}</div>
      )}

      {suggestions.isPending ? <Loading what="suggestions" />
        : !suggestions.data?.length ? <Empty>No prefix is shared by enough names at these settings.</Empty> : (
          <div className="brand-list">
            {suggestions.data.map((row) => (
              <details key={row.prefix} className="brand-row suggestion-row">
                {/* The names are the decision: a prefix covering five branches of one shop is a brand,
                    one covering five unrelated shops that start with the same word is not. Open the
                    row to read them before you commit to it. */}
                <summary>
                  <code className="suggestion-prefix" title={row.prefix}>{row.prefix}</code>
                  <span className="config-hint">
                    {num(row.count)} name(s) · {num(row.names.reduce((sum, name) => sum + receiptsOf(name), 0))} receipt(s)
                  </span>
                  <span className="row-actions">
                    <button disabled={create.isPending} onClick={press(() => create.mutate(row.prefix))}>
                      New brand
                    </button>
                    <button disabled={!target || add.isPending} onClick={press(() => add.mutate(row.prefix))}>
                      Add prefix
                    </button>
                  </span>
                </summary>
                <ul className="file-list suggestion-names">
                  {row.names.map((name) => (
                    <li key={name}>
                      {name} <span className="config-hint">{num(receiptsOf(name))} receipt(s)</span>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
        )}
    </Card>
  )
}

/**
 * brand -> prefix -> branch, with what each one actually matched.
 *
 * A brand is only as good as its prefixes, and a prefix is only visible through the branches it
 * resolved to: this is where you see that one prefix carries the whole brand, that another matched
 * nothing and can go, or that a branch name is really a different shop that shouldn't be here.
 *
 * Nothing here folds. Opening the brand is the one thing you decided to do; making you open each
 * prefix as well hides the comparison between them, which is the whole reason to look. The list gets
 * long on a big brand, and the page scrolls — that is the cheaper cost.
 */
function BrandTree({ tree }: { tree: Brand['tree'] }) {
  if (tree.length === 0) return null
  return (
    <div className="brand-tree">
      {tree.map((node) => (
        <div key={node.prefix} className="brand-tree__prefix">
          <div className="brand-tree__head">
            <code className="suggestion-prefix" title={node.prefix}>{node.prefix}</code>
            <span className="config-hint">
              {node.branches.length === 0 ? 'matches nothing'
                : `${num(node.branches.length)} branch(es) · ${num(node.receipts)} receipt(s)`}
            </span>
          </div>
          {node.branches.length > 0 && (
            <ul className="brand-tree__branches">
              {node.branches.map((branch) => (
                <li key={branch.location}>
                  {/* An empty remainder means the receipt name IS the prefix — no branch in it. */}
                  <span className={branch.location ? '' : 'config-hint'}>
                    {branch.location || 'no branch in the name'}
                  </span>
                  <span className="config-hint">{num(branch.receipts)} receipt(s)</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}

/** A button inside a <summary>: act, and don't let the click open or close the row as well. */
const press = (run: () => void) => (event: MouseEvent) => {
  event.preventDefault()
  event.stopPropagation()
  run()
}

function ManageBrands({ brands }: { brands: Brand[] }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState({ label: '', prefixes: '' })
  const done = () => queryClient.invalidateQueries({ queryKey: ['brands'] })
  const create = useMutation({
    mutationFn: () => api.brands.create({ label: adding.label, prefixes: adding.prefixes.split('\n') }),
    onSuccess: () => {
      setAdding({ label: '', prefixes: '' })
      void done()
    },
  })
  const needle = search.trim().toLowerCase()
  const shown = needle
    ? brands.filter((b) => b.label.toLowerCase().includes(needle) || b.prefixes.some((p) => p.toLowerCase().includes(needle)))
    : brands

  return (
    <>
      <Card title="Brands" hint={`${brands.length} total`}>
        <div className="field">
          <label htmlFor="brand-search">Search</label>
          <input id="brand-search" type="text" value={search} placeholder="label or prefix"
            onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="brand-list">
          {shown.length === 0 ? <Empty>No brand matches.</Empty>
            : shown.map((brand) => <BrandRow key={brand.id} brand={brand} onDone={done} />)}
        </div>
      </Card>

      <Card title="Add a brand">
        <div className="curate-grid">
          <div className="field">
            <label htmlFor="new-label">Label</label>
            <input id="new-label" type="text" value={adding.label}
              onChange={(e) => setAdding({ ...adding, label: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="new-prefixes">Prefixes (one per line)</label>
            <textarea id="new-prefixes" rows={3} value={adding.prefixes}
              onChange={(e) => setAdding({ ...adding, prefixes: e.target.value })} />
          </div>
        </div>
        {create.error && <div className="error-banner" role="alert">{create.error.message}</div>}
        <div className="start-bar">
          <button className="primary" disabled={!adding.label.trim() || !adding.prefixes.trim() || create.isPending}
            onClick={() => create.mutate()}>Add brand</button>
        </div>
      </Card>
    </>
  )
}

function BrandRow({ brand, onDone }: { brand: Brand; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState({ label: brand.label, prefixes: brand.prefixes.join('\n') })
  const [confirming, setConfirming] = useState(false)
  const save = useMutation({
    mutationFn: () => api.brands.update(brand.id, { label: draft.label, prefixes: draft.prefixes.split('\n') }),
    onSuccess: onDone,
  })
  const remove = useMutation({
    mutationFn: () => api.brands.remove(brand.id),
    onSuccess: () => {
      setConfirming(false)
      onDone()
    },
  })
  const changed = draft.label !== brand.label || draft.prefixes !== brand.prefixes.join('\n')

  return (
    <details className="brand-row" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        <strong>{brand.label}</strong>
        <span className="config-hint">{brand.prefixes.length} prefix(es) · {num(brand.receipt_count)} receipt(s)</span>
      </summary>
      <div className="curate-grid">
        <div className="field">
          <label>Label</label>
          <input type="text" value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
        </div>
        <div className="field">
          <label>Prefixes (one per line)</label>
          <textarea rows={Math.max(2, brand.prefixes.length)} value={draft.prefixes}
            onChange={(e) => setDraft({ ...draft, prefixes: e.target.value })} />
        </div>
      </div>

      <BrandTree tree={brand.tree} />

      {(save.error || remove.error) && (
        <div className="error-banner" role="alert">{(save.error ?? remove.error)?.message}</div>
      )}
      <div className="start-bar">
        <button className="primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>Save</button>
        <button className="danger-outline" disabled={remove.isPending} onClick={() => setConfirming(true)}>Delete</button>
      </div>
      {confirming && (
        <ConfirmDialog title={`Delete ${brand.label}?`} confirmLabel="Delete" danger busy={remove.isPending}
          onConfirm={() => remove.mutate()} onCancel={() => setConfirming(false)}>
          Its {num(brand.receipt_count)} receipt(s) stay exactly where they are — they just stop being grouped
          under this brand in the charts. You can add the brand back at any time.
        </ConfirmDialog>
      )}
    </details>
  )
}
