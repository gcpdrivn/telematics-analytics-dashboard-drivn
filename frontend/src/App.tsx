import { NavLink, Route, Routes, useLocation } from "react-router-dom"
import { DateRangePicker } from "./components/DateRangePicker"
import { ThemeToggle } from "./components/ThemeToggle"
import { BusesPage } from "./pages/BusesPage"
import { CustomersPage } from "./pages/CustomersPage"
import { OverviewPage } from "./pages/OverviewPage"
import { TrucksPage } from "./pages/TrucksPage"

function navClass({ isActive }: { isActive: boolean }) {
  return `seg-pill${isActive ? " active" : ""}`
}

export default function App() {
  // Preserve ?start=&end= across page nav -- a plain `to="/buses"` would
  // otherwise drop the date filter every time you switch tabs.
  const location = useLocation()

  return (
    <div className="wrapper">
      <header className="header">
        <div>
          <div className="brand-badge">
            <span className="brand-logo">
              DRIVN<span className="brand-dot" />
            </span>
            <span className="brand-tagline">Fleet Telematics</span>
          </div>
        </div>
        <div className="header-controls">
          <nav className="header-segmented-nav">
            <NavLink to={{ pathname: "/", search: location.search }} end className={navClass}>
              Overview
            </NavLink>
            <NavLink to={{ pathname: "/buses", search: location.search }} className={navClass}>
              Buses
            </NavLink>
            <NavLink to={{ pathname: "/trucks", search: location.search }} className={navClass}>
              Trucks
            </NavLink>
            <NavLink to={{ pathname: "/customers", search: location.search }} className={navClass}>
              Customers
            </NavLink>
          </nav>
          <DateRangePicker />
          <ThemeToggle />
        </div>
      </header>

      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/buses" element={<BusesPage />} />
        <Route path="/trucks" element={<TrucksPage />} />
        <Route path="/customers" element={<CustomersPage />} />
      </Routes>
    </div>
  )
}
