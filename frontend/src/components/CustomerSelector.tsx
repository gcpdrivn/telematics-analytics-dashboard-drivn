import type { CrosstabCustomer } from "../api/types"
import { ALL_CUSTOMERS } from "./CustomerCharts/colors"

/** Shared pill-button customer picker -- extracted from the near-identical
 * markup that used to be duplicated in CrosstabMatrix, UptimeTable, and the
 * trajectory section of CustomersPage. */
export function CustomerSelector({
  value,
  onChange,
  includeAll = true,
}: {
  value: CrosstabCustomer
  onChange: (c: CrosstabCustomer) => void
  includeAll?: boolean
}) {
  const options: CrosstabCustomer[] = includeAll ? ["All", ...ALL_CUSTOMERS] : ALL_CUSTOMERS

  return (
    <div className="customer-nav">
      {options.map((c) => (
        <button key={c} className={`seg-pill ${value === c ? "active" : ""}`} onClick={() => onChange(c)}>
          {c}
        </button>
      ))}
    </div>
  )
}
