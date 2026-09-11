import type {
  Category,
  CrosstabCustomer,
  CrosstabResponse,
  CustomerAnalytics,
  CustomerName,
  DateRangeResponse,
  KpiSummary,
  Scope,
  TrajectoryResponse,
  VehiclesResponse,
} from "./types"

/** Every page re-fetches everything on mount, so switching back to a tab you
 * were just on re-hits the network for identical data. Cache resolved
 * responses (keyed by URL) for a short TTL -- short enough that you still see
 * fresh data reasonably quickly, long enough that tab-switching within that
 * window is instant. Caching the in-flight promise (not just the resolved
 * value) also dedupes concurrent identical requests, e.g. React StrictMode's
 * double-invoke in dev. */
const CACHE_TTL_MS = 60_000
const cache = new Map<string, { promise: Promise<unknown>; expiresAt: number }>()

function getJSON<T>(path: string): Promise<T> {
  const cached = cache.get(path)
  if (cached && cached.expiresAt > Date.now()) {
    return cached.promise as Promise<T>
  }

  const promise = fetch(path).then((res) => {
    if (!res.ok) {
      cache.delete(path)
      throw new Error(`${path} -> ${res.status} ${res.statusText}`)
    }
    return res.json()
  })

  cache.set(path, { promise, expiresAt: Date.now() + CACHE_TTL_MS })
  return promise as Promise<T>
}

/** Absent (null) means "full available range" -- matches the backend's own
 * default when start_date/end_date aren't passed at all, so leaving the date
 * picker untouched behaves exactly like it did before this filter existed. */
export interface DateRange {
  start: string | null
  end: string | null
}

function withDateRange(params: URLSearchParams, range?: DateRange): URLSearchParams {
  if (range?.start) params.set("start_date", range.start)
  if (range?.end) params.set("end_date", range.end)
  return params
}

export function getKpiSummary(scope: Scope, range?: DateRange): Promise<KpiSummary> {
  const params = withDateRange(new URLSearchParams({ scope }), range)
  return getJSON(`/api/kpi-summary?${params}`)
}

export function getCustomerAnalytics(range?: DateRange): Promise<CustomerAnalytics> {
  const params = withDateRange(new URLSearchParams(), range)
  return getJSON(`/api/customers/analytics?${params}`)
}

export function getCrosstabMatrix(
  customer: CrosstabCustomer,
  range?: DateRange
): Promise<CrosstabResponse> {
  const params = withDateRange(new URLSearchParams({ customer }), range)
  return getJSON(`/api/crosstab-matrix?${params}`)
}

export function getVehicles(category: Category, range?: DateRange): Promise<VehiclesResponse> {
  const params = withDateRange(new URLSearchParams({ category }), range)
  return getJSON(`/api/vehicles?${params}`)
}

export function getVehicleTrajectories(
  customer: CustomerName,
  range?: DateRange
): Promise<TrajectoryResponse> {
  const params = withDateRange(new URLSearchParams({ customer }), range)
  return getJSON(`/api/vehicle-trajectories?${params}`)
}

export function getDateRange(): Promise<DateRangeResponse> {
  return getJSON(`/api/date-range`)
}
