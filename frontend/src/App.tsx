import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { StageCount, useSidebarCounts, type Stage } from './components/status/sidebarCounts.tsx'
import Dashboard from './pages/viz/Dashboard.tsx'
import Merchant from './pages/viz/Merchant.tsx'
import CalendarPage from './pages/viz/CalendarPage.tsx'
import TimeCapsule from './pages/viz/TimeCapsule.tsx'
import Receipt from './pages/viz/Receipt.tsx'
import Review from './pages/ingest/Review.tsx'
import FileIndex from './pages/ingest/FileIndex.tsx'
import FixRotation from './pages/ingest/FixRotation.tsx'
import Slice from './pages/ingest/Slice.tsx'
import Group from './pages/ingest/Group.tsx'
import Ocr from './pages/ingest/Ocr.tsx'
import Parse from './pages/ingest/Parse.tsx'
import Archive from './pages/ingest/Archive.tsx'
import Brands from './pages/curate/Brands.tsx'
import Dedupe from './pages/curate/Dedupe.tsx'
import Normalize from './pages/curate/Normalize.tsx'
import Unnamed from './pages/curate/Unnamed.tsx'
import Workshop from './pages/curate/Workshop.tsx'
import Config from './pages/settings/Config.tsx'
import Experiment from './pages/dev/Experiment.tsx'
import SanityCheck from './pages/dev/SanityCheck.tsx'
import IndexAudit from './pages/dev/IndexAudit.tsx'
import FixPreview from './pages/dev/FixPreview.tsx'
import History from './pages/History.tsx'
import { HistoryPip } from './components/status/historyPip.tsx'
import { JobIndicator, JobWatcher } from './components/status/jobs.tsx'

// Ordered the way the work runs: Ingest -> Curate -> Visualize. Settings is for
// everyone; the Dev pages, for working on the app itself, come after it.
const NAV: { group: string; items: { to: string; label: string; stage?: Stage }[] }[] = [
  {
    group: 'Ingest',
    items: [
      { to: '/file-index', label: 'File Index', stage: 'unindexed' },
      { to: '/fix-rotation', label: 'Fix Rotation', stage: 'rotation' },
      { to: '/slice', label: 'Slice' },
      { to: '/group', label: 'Group' },
      { to: '/ocr', label: 'OCR', stage: 'ocr' },
      { to: '/parse', label: 'Parse', stage: 'parse' },
      { to: '/review', label: 'Review', stage: 'review' },
      { to: '/archive', label: 'Archive', stage: 'archive' },
    ],
  },
  {
    group: 'Curate',
    items: [
      { to: '/workshop', label: 'Marked Workshop', stage: 'workshop' },
      { to: '/dedupe', label: 'Dedupe', stage: 'dedupe' },
      { to: '/normalize', label: 'Normalize', stage: 'normalize' },
      { to: '/brands', label: 'Brand registry', stage: 'brands' },
      { to: '/unnamed', label: 'Unnamed', stage: 'unnamed' },
    ],
  },
  {
    group: 'Visualize',
    items: [
      { to: '/dashboard', label: 'Dashboard' },
      { to: '/merchant', label: 'Merchant Profile' },
      // Receipt Detail is deliberately not in the nav: it needs a selected
      // document, so it's reached by clicking any receipt card.
      { to: '/timecapsule', label: 'Time Capsule' },
      { to: '/calendar', label: 'Calendar' },
    ],
  },
  {
    group: 'Settings',
    items: [{ to: '/config', label: 'Config' }],
  },
  {
    group: 'Dev',
    items: [
      { to: '/experiment', label: 'Experiment' },
      { to: '/sanity-check', label: 'Sanity Check' },
      { to: '/index-audit', label: 'Index Audit' },
      { to: '/fix-preview', label: 'Fix Preview' },
    ],
  },
]

export default function App() {
  const counts = useSidebarCounts()
  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">
          Papertrail
          <small>Ex vestigiis veritas</small>
        </div>
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="navgroup">{g.group}</div>
            {g.items.map((it) => (
              <NavLink
                key={it.to}
                to={it.to}
                className={({ isActive }) => `navlink${isActive ? ' active' : ''}`}
              >
                {it.label}
                {it.stage && <StageCount counts={counts} stage={it.stage} />}
              </NavLink>
            ))}
          </div>
        ))}
        <JobIndicator />
        <HistoryPip />
      </nav>
      <JobWatcher />

      {/* No width wrapper: every page is as wide as the window and bounds its own
          blocks (see the WIDTH note at the top of styles.css). */}
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/merchant" element={<Merchant />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/timecapsule" element={<TimeCapsule />} />
          <Route path="/receipt" element={<Receipt />} />
          <Route path="/file-index" element={<FileIndex />} />
          <Route path="/fix-rotation" element={<FixRotation />} />
          <Route path="/slice" element={<Slice />} />
          <Route path="/group" element={<Group />} />
          <Route path="/ocr" element={<Ocr />} />
          <Route path="/parse" element={<Parse />} />
          <Route path="/review" element={<Review />} />
          <Route path="/archive" element={<Archive />} />
          <Route path="/workshop" element={<Workshop />} />
          <Route path="/dedupe" element={<Dedupe />} />
          <Route path="/normalize" element={<Normalize />} />
          <Route path="/brands" element={<Brands />} />
          <Route path="/unnamed" element={<Unnamed />} />
          <Route path="/experiment" element={<Experiment />} />
          <Route path="/sanity-check" element={<SanityCheck />} />
          <Route path="/index-audit" element={<IndexAudit />} />
          <Route path="/fix-preview" element={<FixPreview />} />
          <Route path="/config" element={<Config />} />
          <Route path="/history" element={<History />} />
        </Routes>
      </main>
    </div>
  )
}
