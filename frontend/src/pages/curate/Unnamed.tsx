import { useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, mediaUrl } from '../../api/client.ts'
import type { NamingDocument, UnnamedPage } from '../../api/types.ts'
import { afterArchiveEdit } from '../../api/invalidate.ts'
import { DocumentCard, ReceiptGallery } from '../../components/scans/DocumentCard.tsx'
import { Empty, ErrorState, Loading, Tile } from '../../components/ui.tsx'
import { money, num } from '../../format.ts'
import './curate.css'

/**
 * The documents filed under no name ("Receipt", "Document"): the extractor found none and nobody typed one.
 * They are laid out together so the ones from one shop can be picked out by eye and named in one go.
 *
 * Naming changes only the name a document is filed under (and so its file names): what the extractor read
 * stays, so a document named here joins the named by hand, the scans the extractor couldn't name with the
 * name a person gave them. Those are the second view: what a better extraction prompt is tried against.
 *
 * A document named here stays where it was, marked named, until the page is opened again: the grid doesn't
 * close up under the pointer, and a slip is one click away from being named again. The page asks the server
 * for those documents as they are filed now (`keep`), since naming one can renumber the files of another
 * that shared its name.
 */
const UNNAMED = ['curate', 'unnamed'] as const

type View = 'unnamed' | 'byHand'
type Size = 'S' | 'M' | 'L'
/** A card's narrowest width at each size (the grid fits as many as the window takes). */
const COLUMN: Record<Size, string> = { S: '8.25rem', M: '11rem', L: '15rem' }

/** The server's order: oldest first, undated last. */
const byDate = (a: NamingDocument, b: NamingDocument) =>
  Number(a.date === '') - Number(b.date === '') || a.date.localeCompare(b.date) || a.time.localeCompare(b.time)
  || a.filename.localeCompare(b.filename)

/** One naming, as it can be taken back: each document's name before it. */
type Undo = { name: string; previous: { document: string; name: string }[] }

export default function Unnamed() {
  const queryClient = useQueryClient()
  /** Documents named (or named back) on this visit, which keep their place in the grid. Read by the query
   *  itself rather than keyed on: the list is this visit's, and a new key would show a loading page. */
  const touched = useRef(new Set<string>())
  const query = useQuery({
    queryKey: UNNAMED, queryFn: () => api.curate.unnamed([...touched.current]),
    gcTime: 0,                       // a new visit starts with nothing kept, not the last visit's list
  })
  const [view, setView] = useState<View>('unnamed')
  const [size, setSize] = useState<Size>('M')
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [anchor, setAnchor] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [undoable, setUndoable] = useState<Undo | null>(null)
  /** Why the last naming or undo didn't do all it was asked: said in the name bar's hint, which is always there. */
  const [problem, setProblem] = useState<string | null>(null)

  const shown = useMemo(() => {
    const kept = new Map((query.data?.kept ?? []).map((d) => [d.filename, d]))
    return [...(query.data?.unnamed ?? []).filter((d) => !kept.has(d.filename)), ...kept.values()].sort(byDate)
  }, [query.data])

  /** Show what a naming did right away, then read the page again: the server has every file as it is now. */
  const settle = (docs: NamingDocument[]) => {
    const keys = new Set(docs.map((d) => d.filename))
    for (const key of keys) touched.current.add(key)
    queryClient.setQueryData<UnnamedPage>(UNNAMED, (old) => old && {
      ...old,
      unnamed: old.unnamed.filter((d) => !keys.has(d.filename)),
      kept: [...old.kept.filter((d) => !keys.has(d.filename)), ...docs],
    })
    afterArchiveEdit(queryClient, UNNAMED)
  }

  const nameThem = useMutation({
    mutationFn: ({ documents, to }: { documents: string[]; to: string }) => api.curate.nameDocuments(documents, to),
    onMutate: () => setProblem(null),
    onSuccess: (result, { to }) => {
      const named = new Set(result.named.map((n) => n.document.filename))
      settle(result.named.map((n) => n.document))
      // a naming that stopped part-way can still be taken back for what it did
      if (named.size) {
        setUndoable({ name: to.trim(), previous: result.named.map((n) => ({ document: n.document.filename,
          name: n.previous_name })) })
      }
      setSelected((before) => new Set([...before].filter((key) => !named.has(key))))   // the rest stay picked
      if (result.error) setProblem(result.error)
      else { setName(''); setAnchor(null) }
    },
    onError: (error) => setProblem(error.message),
  })
  const undo = useMutation({
    mutationFn: async (taken: Undo) => {
      // one call per name the documents had (a naming usually takes one name back: the placeholder); what the
      // calls before a failure took back is kept
      const byName = new Map<string, string[]>()
      for (const { document, name: was } of taken.previous) byName.set(was, [...byName.get(was) ?? [], document])
      const named: NamingDocument[] = []
      try {
        for (const [was, documents] of byName) {
          const result = await api.curate.nameDocuments(documents, was)
          named.push(...result.named.map((n) => n.document))
          if (result.error) return { named, error: result.error }
        }
      } catch (error) {
        return { named, error: error instanceof Error ? error.message : String(error) }
      }
      return { named, error: null }
    },
    onMutate: () => setProblem(null),
    onSuccess: ({ named, error }, taken) => {
      settle(named)
      const back = new Set(named.map((d) => d.filename))
      const rest = taken.previous.filter((p) => !back.has(p.document))
      setUndoable(rest.length ? { ...taken, previous: rest } : null)
      if (error) setProblem(error)
    },
  })
  const busy = nameThem.isPending || undo.isPending

  if (query.isPending) return <Loading what="unnamed documents" />
  if (query.error) return <ErrorState error={query.error} />
  const data = query.data
  const counts = new Map(data.names.map((n) => [n.name, n.count]))
  const stillUnnamed = shown.filter((d) => d.unnamed).length
  const namedHere = data.kept.filter((d) => !d.unnamed).length
  const picked = shown.filter((d) => selected.has(d.filename)).map((d) => d.filename)   // in the grid's order
  const typed = name.trim()
  const known = counts.get(typed)

  /** Click toggles a card; shift-click sets every card from the last one clicked to this one as that one is. */
  const pick = (filename: string, range: boolean) => {
    const on = !selected.has(filename)
    const from = range && anchor !== null ? shown.findIndex((d) => d.filename === anchor) : -1
    const to = shown.findIndex((d) => d.filename === filename)
    const span = from >= 0 ? shown.slice(Math.min(from, to), Math.max(from, to) + 1).map((d) => d.filename) : [filename]
    const next = new Set(selected)
    for (const key of span) {
      if (on) next.add(key)
      else next.delete(key)
    }
    setSelected(next)
    setAnchor(filename)
  }
  const submit = () => {
    if (picked.length && typed && !busy) nameThem.mutate({ documents: picked, to: typed })
  }

  return (
    <div className="curate-page naming-page" style={{ '--naming-col': COLUMN[size] } as CSSProperties}>
      <h1>Unnamed</h1>
      <p className="page-sub">
        Documents filed under no name: the extractor found none and nobody typed one in. Pick out the ones from
        one shop and name them together. Only the name changes; what the extractor read is kept.
      </p>

      <div className="tiles">
        <Tile label="Unnamed" value={num(stillUnnamed)} />
        <Tile label="Named on this visit" value={num(namedHere)} />
        <Tile label="Named by hand" value={num(data.named_by_hand.length)} />
      </div>

      <div className="controls">
        <div className="segmented" role="tablist">
          <button role="tab" aria-selected={view === 'unnamed'} className={view === 'unnamed' ? 'on' : ''}
            onClick={() => setView('unnamed')}>Unnamed</button>
          <button role="tab" aria-selected={view === 'byHand'} className={view === 'byHand' ? 'on' : ''}
            onClick={() => setView('byHand')}>Named by hand</button>
        </div>
        <div className="segmented" aria-label="Scan size">
          {(Object.keys(COLUMN) as Size[]).map((s) => (
            <button key={s} className={size === s ? 'on' : ''} title={`${s === 'S' ? 'Small' : s === 'M' ? 'Medium' : 'Large'} scans`}
              onClick={() => setSize(s)}>{s}</button>
          ))}
        </div>
      </div>

      {view === 'unnamed' ? (
        <>
          <form className="naming-bar" onSubmit={(e) => { e.preventDefault(); submit() }}>
            <input type="text" list="naming-names" value={name} onChange={(e) => { setName(e.target.value); setProblem(null) }}
              placeholder="Name for the selected" aria-label="Name for the selected documents" />
            <datalist id="naming-names">
              {data.names.map((n) => <option key={n.name} value={n.name}>{`${n.count} document(s)`}</option>)}
            </datalist>
            <button type="submit" className="primary" disabled={!picked.length || !typed || busy}>
              Name {picked.length || ''} {picked.length === 1 ? 'document' : 'documents'}
            </button>
            <button type="button" disabled={!picked.length} onClick={() => { setSelected(new Set()); setAnchor(null) }}>
              Select none
            </button>
            <span className={`config-hint naming-hint${problem ? ' naming-hint--problem' : ''}`}
              role={problem ? 'alert' : undefined}>
              {problem ?? (!typed ? 'Click a scan to select it, shift-click for a run of them; ⤢ shows one full size.'
                : data.placeholders.includes(typed) ? 'Not a name: files them under no name again.'
                  : known !== undefined ? `A name in use: ${num(known)} document(s).`
                    : 'A new name: no document is filed under it yet.')}
            </span>
          </form>

          {shown.length === 0 ? <Empty>Nothing is filed under no name.</Empty> : (
            <div className="receipt-gallery naming-grid">
              {shown.map((doc) => (
                <NamingCard key={doc.filename} doc={doc} selected={selected.has(doc.filename)}
                  onPick={(range) => pick(doc.filename, range)} />
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <p className="config-hint naming-note">
            Read with no name, and filed under the name you gave them, here or anywhere else: each one a scan the
            extractor couldn't name, beside its name.
          </p>
          {data.named_by_hand.length === 0 ? <Empty>No document read without a name has been named yet.</Empty>
            : <ReceiptGallery receipts={data.named_by_hand} />}
        </>
      )}

      {undoable && (
        <div className="toast" role="status">
          Named {num(undoable.previous.length)} {undoable.previous.length === 1 ? 'document' : 'documents'}
          {' '}<strong>{undoable.name}</strong>
          <button disabled={busy} onClick={() => undo.mutate(undoable)}>Undo</button>
          <button onClick={() => setUndoable(null)}>Dismiss</button>
        </div>
      )}
    </div>
  )
}

/** A document to pick: the card is a toggle button, its zoom button beside it shows the scan full size. */
function NamingCard({ doc, selected, onPick }: { doc: NamingDocument; selected: boolean; onPick: (range: boolean) => void }) {
  const amount = doc.document_type === 'receipt' ? money(doc.cost, doc.currency) : doc.document_type
  const when = doc.date ? `${doc.date}${doc.time ? ` ${doc.time.slice(0, 5)}` : ''}` : 'undated'
  return (
    <DocumentCard as="button" type="button" aria-pressed={selected} aria-label={`${doc.name}, ${when}, ${amount}`}
      className={`naming-card${doc.unnamed ? '' : ' is-named'}`}
      src={mediaUrl(doc.path)} trim={doc.trim}
      name={doc.unnamed ? <span className="naming-card__none">{doc.name}</span> : <>✓ {doc.name}</>}
      caption={<>
        {when} · {amount}{doc.pages > 1 ? ` · ${doc.pages} pages` : ''}
        {doc.read && doc.unnamed ? <><br />read as “{doc.read}”</> : null}
      </>}
      title={doc.unnamed ? undefined : `Named ${doc.name} on this visit: pick it to name it again`}
      onClick={(e: MouseEvent) => onPick(e.shiftKey)}>
      <span className="naming-card__check" aria-hidden="true">✓</span>
    </DocumentCard>
  )
}
