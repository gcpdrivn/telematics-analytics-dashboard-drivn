import { useEffect, useRef, useState } from "react"
import { getDateRange } from "../api/client"
import { useDateRange } from "../hooks/useDateRange"

const PRESETS = [
  { label: "Last 7 days", days: 7 },
  { label: "Last 15 days", days: 15 },
  { label: "Last 30 days", days: 30 },
]

function addDaysISO(iso: string, days: number): string {
  // Pure local calendar-date arithmetic -- deliberately never touches
  // toISOString()/UTC. `new Date(iso + "T00:00:00")` parses as local
  // midnight, but round-tripping through toISOString() converts back to
  // UTC, which silently shifts the date backward by a day in any timezone
  // ahead of UTC (including IST) -- e.g. addDaysISO("2026-09-10", -6) was
  // returning "2026-09-09" instead of "2026-09-04", breaking the "Last 7
  // days" preset match. Using the (year, month, day) constructor plus local
  // getters end to end avoids UTC entirely.
  const [y, m, d] = iso.split("-").map(Number)
  const date = new Date(y, m - 1, d)
  date.setDate(date.getDate() + days)
  const yyyy = date.getFullYear()
  const mm = String(date.getMonth() + 1).padStart(2, "0")
  const dd = String(date.getDate()).padStart(2, "0")
  return `${yyyy}-${mm}-${dd}`
}

export function DateRangePicker() {
  const [range, setRange] = useDateRange()
  const [bounds, setBounds] = useState<{ min: string; max: string } | null>(null)
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getDateRange().then((d) => setBounds({ min: d.min_date, max: d.max_date }))
  }, [])

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", onClickOutside)
    return () => document.removeEventListener("mousedown", onClickOutside)
  }, [])

  if (!bounds) return null

  function applyPreset(days: number) {
    if (!bounds) return
    const end = bounds.max
    const start = addDaysISO(end, -(days - 1))
    setRange(start < bounds.min ? bounds.min : start, end)
    setOpen(false)
  }

  function clearRange() {
    setRange(null, null)
    setOpen(false)
  }

  const isFullRange = !range.start && !range.end
  const activePreset = PRESETS.find((p) => {
    if (isFullRange) return false
    const expectedStart = addDaysISO(bounds.max, -(p.days - 1))
    const clampedStart = expectedStart < bounds.min ? bounds.min : expectedStart
    return range.start === clampedStart && range.end === bounds.max
  })

  const label = isFullRange
    ? "Full range"
    : (activePreset?.label ?? `${range.start ?? bounds.min} → ${range.end ?? bounds.max}`)

  return (
    <div className="date-range-picker" ref={containerRef}>
      <button className="btn-action" onClick={() => setOpen((o) => !o)}>
        📅 {label}
      </button>
      {open && (
        <div className="date-range-dropdown panel">
          <div className="date-range-presets">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                className={`seg-pill ${activePreset?.label === p.label ? "active" : ""}`}
                onClick={() => applyPreset(p.days)}
              >
                {p.label}
              </button>
            ))}
            <button className={`seg-pill ${isFullRange ? "active" : ""}`} onClick={clearRange}>
              Full range
            </button>
          </div>
          <div className="date-range-custom">
            <label>
              From
              <input
                type="date"
                min={bounds.min}
                max={range.end ?? bounds.max}
                value={range.start ?? bounds.min}
                onChange={(e) => setRange(e.target.value, range.end)}
              />
            </label>
            <label>
              To
              <input
                type="date"
                min={range.start ?? bounds.min}
                max={bounds.max}
                value={range.end ?? bounds.max}
                onChange={(e) => setRange(range.start, e.target.value)}
              />
            </label>
          </div>
          <div className="date-range-bounds muted">
            Data available: {bounds.min} to {bounds.max}
          </div>
        </div>
      )}
    </div>
  )
}
