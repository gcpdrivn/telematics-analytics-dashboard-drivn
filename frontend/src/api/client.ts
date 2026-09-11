import type {
  Category,
  CrosstabCustomer,
  CrosstabResponse,
  CustomerAnalytics,
  CustomerName,
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

export function getKpiSummary(scope: Scope): Promise<KpiSummary> {
  return getJSON(`/api/kpi-summary?scope=${scope}`)
}

export function getCustomerAnalytics(): Promise<CustomerAnalytics> {
  return getJSON(`/api/customers/analytics`)
}

export function getCrosstabMatrix(customer: CrosstabCustomer): Promise<CrosstabResponse> {
  return getJSON(`/api/crosstab-matrix?customer=${customer}`)
}

export function getVehicles(category: Category): Promise<VehiclesResponse> {
  return getJSON(`/api/vehicles?category=${category}`)
}

export function getVehicleTrajectories(customer: CustomerName): Promise<TrajectoryResponse> {
  return getJSON(`/api/vehicle-trajectories?customer=${customer}`)
}
