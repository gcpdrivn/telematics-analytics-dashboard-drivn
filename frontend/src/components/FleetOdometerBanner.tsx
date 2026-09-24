import { useEffect, useState } from "react"
import { getFleetOdometerTotal } from "../api/client"
import type { FleetOdometerTotal } from "../api/types"
import { formatIndian } from "./KpiCards"

/** Whole-fleet lifetime distance from final odometer readings. Rendered beside
 * the logo, outside the filters, and fetched with no date/scope params, so it never
 * changes with the date picker, page tab, or customer selector. */
export function FleetOdometerBanner() {
  const [data, setData] = useState<FleetOdometerTotal | null>(null)

  useEffect(() => {
    getFleetOdometerTotal()
      .then(setData)
      .catch(() => setData(null))
  }, [])

  return (
    <div className="fleet-odo-banner">
      <span className="fleet-odo-label">Total Lifetime Distance</span>
      <span className="fleet-odo-num">
        {data ? formatIndian(data.total_odometer) : "—"} <span className="kpi-unit">km</span>
      </span>
      {data && (
        <span className="fleet-odo-meta">
          as of {data.as_of_date}
        </span>
      )}
    </div>
  )
}
