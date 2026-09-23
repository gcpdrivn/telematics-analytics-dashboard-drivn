import { useSearchParams } from "react-router-dom"
import type { CrosstabCustomer } from "../api/types"

/** URL-backed (?customer=), mirroring useDateRange -- survives refresh,
 * supports back/forward, and is shareable via link. Absent means "All". */
export function useCustomerFilter(): [CrosstabCustomer, (c: CrosstabCustomer) => void] {
  const [searchParams, setSearchParams] = useSearchParams()

  const customer = (searchParams.get("customer") as CrosstabCustomer | null) ?? "All"

  function setCustomer(c: CrosstabCustomer) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (c && c !== "All") next.set("customer", c)
        else next.delete("customer")
        return next
      },
      { replace: true }
    )
  }

  return [customer, setCustomer]
}
