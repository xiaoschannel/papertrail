import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard.tsx'
import Merchant from './pages/Merchant.tsx'
import CalendarPage from './pages/CalendarPage.tsx'
import TimeCapsule from './pages/TimeCapsule.tsx'
import Receipt from './pages/Receipt.tsx'
import Review from './pages/Review.tsx'
import FileIndex from './pages/FileIndex.tsx'
import Ocr from './pages/Ocr.tsx'
import Parse from './pages/Parse.tsx'
import Archive from './pages/Archive.tsx'
import Brands from './pages/Brands.tsx'
import Dedupe from './pages/Dedupe.tsx'
import Normalize from './pages/Normalize.tsx'
import Workshop from './pages/Workshop.tsx'
import Config from './pages/Config.tsx'
import Experiment from './pages/Experiment.tsx'
import SanityCheck from './pages/SanityCheck.tsx'
import IndexAudit from './pages/IndexAudit.tsx'
import { JobIndicator, JobWatcher } from './components/jobs.tsx'

// Ordered the way the work runs: Ingest -> Curate -> Visualize. Settings is for
// everyone; the Dev pages, for working on the app itself, come after it.
const NAV = [
  {
    group: 'Ingest',
    items: [
      { to: '/file-index', label: 'File Index' },
      { to: '/ocr', label: 'OCR' },
      { to: '/parse', label: 'Parse' },
      { to: '/review', label: 'Review' },
      { to: '/archive', label: 'Archive' },
    ],
  },
  {
    group: 'Curate',
    items: [
      { to: '/workshop', label: 'Marked Workshop' },
      { to: '/dedupe', label: 'Dedupe' },
      { to: '/normalize', label: 'Normalize' },
      { to: '/brands', label: 'Brand registry' },
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
    ],
  },
]

export default function App() {
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
              </NavLink>
            ))}
          </div>
        ))}
        <JobIndicator />
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
          <Route path="/ocr" element={<Ocr />} />
          <Route path="/parse" element={<Parse />} />
          <Route path="/review" element={<Review />} />
          <Route path="/archive" element={<Archive />} />
          <Route path="/workshop" element={<Workshop />} />
          <Route path="/dedupe" element={<Dedupe />} />
          <Route path="/normalize" element={<Normalize />} />
          <Route path="/brands" element={<Brands />} />
          <Route path="/experiment" element={<Experiment />} />
          <Route path="/sanity-check" element={<SanityCheck />} />
          <Route path="/index-audit" element={<IndexAudit />} />
          <Route path="/config" element={<Config />} />
        </Routes>
      </main>
    </div>
  )
}
