import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Dashboard from './pages/Dashboard.tsx'
import Merchant from './pages/Merchant.tsx'
import CalendarPage from './pages/CalendarPage.tsx'
import TimeCapsule from './pages/TimeCapsule.tsx'
import Receipt from './pages/Receipt.tsx'
import Review from './pages/Review.tsx'

const NAV = [
  {
    group: 'Ingest',
    items: [{ to: '/review', label: 'Review' }],
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
]

// Grid and multi-column workspace pages take the full width (see .main-inner.wide).
const WIDE_ROUTES = ['/calendar', '/review']

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
      </nav>

      <main className="main">
        <div className={`main-inner${wide ? ' wide' : ''}`}>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/merchant" element={<Merchant />} />
            <Route path="/calendar" element={<CalendarPage />} />
            <Route path="/timecapsule" element={<TimeCapsule />} />
            <Route path="/receipt" element={<Receipt />} />
            <Route path="/review" element={<Review />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
