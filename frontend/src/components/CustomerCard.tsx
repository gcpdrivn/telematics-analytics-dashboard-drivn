import type { CustomerProfile } from "../api/types"

/** Ports report.html's .customer-card layout (header/badge, route, metric
 * pills, 2x2 stat grid, fleet-share progress bar, insight footer) -- the
 * original card had a lot more information density than a name+route+insight
 * line, so this restores that instead of inventing a thinner layout. */
export function CustomerCard({ customer }: { customer: CustomerProfile }) {
  const c = customer
  return (
    <div className="customer-card">
      <div className="customer-card-header">
        <div>
          <div className="customer-name">{c.customer}</div>
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{c.oem}</div>
        </div>
        <span className="customer-badge">
          {c.vehicle_count} Vehicles • {c.vol_tier} ({c.avg_cv_pct.toFixed(0)}% Volatility)
        </span>
      </div>

      <div className="customer-route">📍 {c.route}</div>

      <div className="customer-pill-stack">
        <div className="customer-metric-pill brand">
          <span>
            🔋 <b>Avg Mileage / Vehicle:</b>
          </span>
          <span style={{ fontWeight: 700, color: "var(--accent-brand)" }}>
            {c.avg_mileage_soc !== null ? `${c.avg_mileage_soc.toFixed(1)} km / SoC` : "—"}
          </span>
        </div>
        <div className="customer-metric-pill">
          <span>
            ⏱️ <b>Operating Hours / Day / Veh:</b>
          </span>
          <span style={{ fontWeight: 700 }}>{c.avg_hrs_day_per_veh.toFixed(1)} hrs/d</span>
        </div>
        <div className="customer-metric-pill">
          <span>
            📈 <b>Daily KM Volatility:</b>
          </span>
          <span className={`volatility-tag ${c.vol_class}`} style={{ fontWeight: 700 }}>
            {c.avg_cv_pct.toFixed(1)}% ({c.vol_tier} • ±{c.avg_std_active.toFixed(0)} km/d)
          </span>
        </div>
      </div>

      <div className="customer-stats">
        <div>
          <div className="stat-item-label">Daily Avg / Veh</div>
          <div className="stat-item-val">
            {c.avg_km_day_per_veh.toFixed(0)} <span style={{ fontSize: "0.75rem" }}>km/d</span>
          </div>
        </div>
        <div>
          <div className="stat-item-label">Total Fleet Dist</div>
          <div className="stat-item-val">
            {c.total_distance.toLocaleString(undefined, { maximumFractionDigits: 0 })}{" "}
            <span style={{ fontSize: "0.75rem" }}>km</span>
          </div>
        </div>
        <div>
          <div className="stat-item-label">Active Days Avg</div>
          <div className="stat-item-val">
            {c.active_days_avg.toFixed(1)} / {c.total_days_avg.toFixed(0)} d
          </div>
        </div>
        <div>
          <div className="stat-item-label">Total Engine Hrs</div>
          <div className="stat-item-val">
            {c.total_hours.toLocaleString(undefined, { maximumFractionDigits: 0 })}{" "}
            <span style={{ fontSize: "0.75rem" }}>h</span>
          </div>
        </div>
      </div>

      <div className="customer-progress">
        <div className="progress-label">
          <span>Fleet Distance Share</span>
          <span style={{ fontWeight: 600 }}>{c.share_pct.toFixed(1)}%</span>
        </div>
        <div className="progress-track">
          <div className="progress-bar" style={{ width: `${Math.min(100, c.share_pct)}%` }} />
        </div>
      </div>

      <div className="customer-footer" dangerouslySetInnerHTML={{ __html: c.insight }} />
    </div>
  )
}
