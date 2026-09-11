import { NavLink, Route, Routes } from "react-router-dom"
import { ThemeToggle } from "./components/ThemeToggle"
import { BusesPage } from "./pages/BusesPage"
import { CustomersPage } from "./pages/CustomersPage"
import { OverviewPage } from "./pages/OverviewPage"
import { TrucksPage } from "./pages/TrucksPage"

function navClass({ isActive }: { isActive: boolean }) {
  return `seg-pill${isActive ? " active" : ""}`
}

export default function App() {
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
            <NavLink to="/" end className={navClass}>
              Overview
            </NavLink>
            <NavLink to="/buses" className={navClass}>
              Buses
            </NavLink>
            <NavLink to="/trucks" className={navClass}>
              Trucks
            </NavLink>
            <NavLink to="/customers" className={navClass}>
              Customers
            </NavLink>
          </nav>
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
