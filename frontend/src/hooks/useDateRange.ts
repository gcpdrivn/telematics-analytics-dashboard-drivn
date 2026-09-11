import { useSearchParams } from "react-router-dom"
import type { DateRange } from "../api/client"

/** URL-backed (not Context/local state) so the range survives refresh,
 * supports browser back/forward, and is shareable via link -- all four pages
 * read the same ?start=&end= params, no prop drilling or provider needed.
 * Absent params mean "full available range", matching the backend's own
 * default when start_date/end_date aren't passed. */
export function useDateRange(): [DateRange, (start: string | null, end: string | null) => void] {
  const [searchParams, setSearchParams] = useSearchParams()

  const range: DateRange = {
    start: searchParams.get("start"),
    end: searchParams.get("end"),
  }

  function setRange(start: string | null, end: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (start) next.set("start", start)
        else next.delete("start")
        if (end) next.set("end", end)
        else next.delete("end")
        return next
      },
      { replace: true }
    )
  }

  return [range, setRange]
}
