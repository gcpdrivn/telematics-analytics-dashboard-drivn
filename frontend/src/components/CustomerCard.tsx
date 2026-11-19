import type { ReactNode } from "react"
import type { CustomerProfile } from "../api/types"

/** Small stroke icons (lucide-style), coloured by `currentColor`. */
function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      {children}
    </svg>
  )
}

const PinIcon = () => (
  <Icon>
    <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
    <circle cx="12" cy="10" r="3" />
  </Icon>
)
const BatteryIcon = () => (
  <Icon>
    <rect x="2" y="7" width="16" height="10" rx="2" />
    <line x1="22" y1="11" x2="22" y2="13" />
    <line x1="6" y1="11" x2="6" y2="13" />
    <line x1="10" y1="11" x2="10" y2="13" />
  </Icon>
)
const ClockIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <polyline points="12 7 12 12 15 14" />
  </Icon>
)
const CalendarIcon = () => (
  <Icon>
    <rect x="3" y="4" width="18" height="18" rx="2" />
    <line x1="16" y1="2" x2="16" y2="6" />
    <line x1="8" y1="2" x2="8" y2="6" />
    <line x1="3" y1="10" x2="21" y2="10" />
  </Icon>
)
const RouteIcon = () => (
  <Icon>
    <circle cx="6" cy="19" r="3" />
    <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" />
    <circle cx="18" cy="5" r="3" />
  </Icon>
)

/** Customer account card ("paired tiles" design): two metric pills, then a
 * Utilisation tile (daily avg km beside active-days %) and a Fleet Distance
 * tile (total km with its share of the fleet). */
export function CustomerCard({ customer }: { customer: CustomerProfile }) {
  const c = customer
  const activePct = c.total_days_avg > 0 ? (c.active_days_avg / c.total_days_avg) * 100 : null

  return (
    <div className="customer-card">
      <div className="customer-card-header">
        <div>
          <div className="customer-name">{c.customer}</div>
          <div className="customer-oem">{c.oem}</div>
        </div>
      </div>

      <div className="customer-route">
        <span className="customer-route-icon">
          <PinIcon />
        </span>
        {c.route}
      </div>

      <div className="customer-pill-stack">
        <div className="customer-metric-pill brand">
          <span className="cc-pill-label">
            <span className="cc-pill-icon brand">
              <BatteryIcon />
            </span>
            Avg Mileage / Vehicle:
          </span>
          <span className="cc-pill-value brand">
            {c.avg_mileage_kwh != null ? `${c.avg_mileage_kwh.toFixed(2)} km / kWh` : "—"}
          </span>
        </div>
        <div className="customer-metric-pill">
          <span className="cc-pill-label">
            <span className="cc-pill-icon">
              <ClockIcon />
            </span>
            Operating Hours / Day / Veh:
          </span>
          <span className="cc-pill-value">{c.avg_hrs_day_per_veh.toFixed(1)} hrs/d</span>
        </div>
      </div>

      <div className="cc-tile">
        <div className="cc-tile-label">
          <CalendarIcon />
          Utilisation / Veh
        </div>
        <div className="cc-split">
          <div>
            <div className="cc-big">
              {c.avg_km_day_per_veh.toFixed(0)} <span className="cc-unit">km/d</span>
            </div>
            <div className="cc-cap">Daily avg</div>
          </div>
          <div className="cc-split-right">
            <div className="cc-big">
              {activePct != null ? activePct.toFixed(1) : "—"}
              <span className="cc-unit">%</span>
            </div>
            <div className="cc-cap">Active days</div>
            <div className="cc-track">
              <div className="cc-bar blue" style={{ width: `${Math.min(100, activePct ?? 0)}%` }} />
            </div>
          </div>
        </div>
      </div>

      <div className="cc-tile">
        <div className="cc-tile-label">
          <RouteIcon />
          Fleet Distance
        </div>
        <div className="cc-row">
          <div className="cc-big">
            {c.total_distance.toLocaleString(undefined, { maximumFractionDigits: 0 })}{" "}
            <span className="cc-unit">km</span>
          </div>
          <div className="cc-share">{c.share_pct.toFixed(1)}%</div>
        </div>
        <div className="cc-track">
          <div className="cc-bar" style={{ width: `${Math.min(100, c.share_pct)}%` }} />
        </div>
        <div className="cc-cap">Share of total fleet distance</div>
      </div>
    </div>
  )
}
