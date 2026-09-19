import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
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
import Config from './pages/Config.tsx'
import { JobIndicator, JobWatcher } from './components/jobs.tsx'

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
]

// Grid and multi-column workspace pages take the full width (see .main-inner.wide).
const WIDE_ROUTES = ['/calendar', '/review', '/file-index']

export default function App() {
  const { pathname } = useLocation()
  const wide = WIDE_ROUTES.some((p) => pathname.startsWith(p))
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

      <main className="main">
        <div className={`main-inner${wide ? ' wide' : ''}`}>
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
            <Route path="/config" element={<Config />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
