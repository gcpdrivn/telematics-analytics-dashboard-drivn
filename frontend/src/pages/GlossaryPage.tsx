import type { ReactNode } from "react"
import { Link, useLocation } from "react-router-dom"

/** Static, plain-language glossary of every term, chart and table on the
 * dashboard. Mirrors the "Drivn Telematics Dashboard — Glossary" doc -- keep
 * the two in step when a metric's definition changes in backend/metrics.py,
 * backend/uptime.py or ingestion/odometer_resolver.py. */

type Row = ReactNode[]

/** Link to a chart/table on a dashboard page, e.g. "/#soh-odometer" or
 * "/buses#vehicle-table" (ids are set on the panels themselves). Keeps the
 * current ?start=&end= so the chart opens on the same date range. */
function ChartLink({ to, children }: { to: string; children: ReactNode }) {
  const { search } = useLocation()
  const [pathname, hash] = to.split("#")
  return (
    <Link className="chart-link" to={{ pathname, search, hash: hash ? `#${hash}` : "" }}>
      {children}
    </Link>
  )
}

function GTable({ head, rows }: { head: string[]; rows: Row[] }) {
  return (
    <div className="glossary-table">
      <table>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section className="panel" id={id}>
      <div className="panel-header">{title}</div>
      <div className="panel-body glossary">{children}</div>
    </section>
  )
}

const SECTIONS = [
  { id: "how-to-read", title: "How to read it" },
  { id: "core-terms", title: "Core terms" },
  { id: "page-by-page", title: "Page by page" },
  { id: "data-quality", title: "Data quality" },
]

export function GlossaryPage() {
  return (
    <>
      <div className="panel filter-bar">
        <span className="filter-bar-label">Glossary</span>
        <nav className="customer-nav">
          {SECTIONS.map((s) => (
            <a key={s.id} href={`#${s.id}`} className="seg-pill">
              {s.title}
            </a>
          ))}
        </nav>
      </div>

      <Section id="how-to-read" title="How to read it">
        <ul>
          <li>
            Every number is built from one row per vehicle per day. <b>A day</b> is an IST calendar day, and a trip
            that crosses midnight counts on the day it ends.
          </li>
          <li>
            <b>The date picker</b> applies to everything except the{" "}
            <ChartLink to="/#lifetime-distance">Total Lifetime Distance</ChartLink> in the header and the{" "}
            <ChartLink to="/#soh-odometer">Battery State of Health vs Latest Odometer</ChartLink> chart.
          </li>
          <li>
            Names in <span className="chart-link-sample">green</span> link to that chart, keeping your date range.
          </li>
        </ul>
      </Section>

      <Section id="core-terms" title="Core terms">
        <GTable
          head={["Term", "What it means"]}
          rows={[
            ["Vehicle-day (\"run\")", "One vehicle on one date with a report from its device. No report, no vehicle-day."],
            ["Active day", "A day the vehicle drove more than 0 km."],
            [
              "Days available",
              "Days in the range since the vehicle was onboarded (device installed, or its first report if no install date is on file). Earlier days never count against it.",
            ],
            [
              "Active rate (% Avail)",
              "Active days ÷ days available. Vehicles under 80% are highlighted in the vehicle tables.",
            ],
            ["Daily distance", "End-of-day minus start-of-day odometer, after faulty readings are repaired."],
            [
              "Avg km/day",
              <>
                Total km ÷ <b>active</b> days: how far a vehicle drives on a day it works. Per-vehicle averages on the
                dashboard all use active days only.
              </>,
            ],
            [
              "Gap distance / Under Operation",
              "Gap distance is km the odometer shows between two reports that can't be pinned to one day, e.g. while the device was silent. Under Operation = daily distance + gap distance.",
            ],
            [
              "Mileage (km/SoC)",
              "Km per 1% of battery, from a fixed benchmark sheet (not the date range). Values of 10+ are dropped as sensor errors.",
            ],
            [
              "Est. Daily SoC",
              "Avg km/day ÷ mileage: the share of a full battery used on a typical active day. Over 100% means more than one charge a day.",
            ],
            ["SoH (State of Health)", "How much charge the battery can still hold compared with when new, in %."],
            [
              "Daily KM Volatility (CV)",
              "σ (the typical swing in daily km) ÷ Avg km/day, over active days. Stable under 25%, Moderate 25–50%, Volatile 50%+. Needs 2 active days; otherwise shown as Single Day (a vehicle) or Yard Holding (a whole customer).",
            ],
          ]}
        />
      </Section>

      <Section id="page-by-page" title="Page by page">
        <h3 id="lifetime-vs-verified">Total Lifetime Distance vs Verified</h3>
        <p>
          The <ChartLink to="/#lifetime-distance">header figure</ChartLink> is the sum of each vehicle's latest good
          odometer reading over the full history, whatever the filters. It only skips readings of 0, above 15 lakh km or
          the stuck value 20,97,151 km, and takes everything else as it stands.
        </p>
        <p>
          <b>Verified</b> (on the <ChartLink to="/#kpi-cards">KPI cards</ChartLink>) rebuilds each vehicle's total:
          its first valid reading (or a corrected value from the vehicle master sheet), plus checked km in the selected
          dates, plus gap distance. It ignores the readings the header skips and any jump or drop of 1,500 km or more
          in a day. It does <b>not</b> catch smaller jumps or drops, quiet days during a reset, or a wrong first
          reading unless someone corrects it.
        </p>
        <GTable
          head={["When", "Header", "Verified"]}
          rows={[
            ["Range is not Full range", "Unchanged", "Lower: only the selected dates' km are added"],
            ["Buses or Trucks page", "Whole fleet", "That page's vehicles only"],
            ["Vehicle didn't drive in the range", "Included", "Left out"],
            ["Odometer jumps 1,500+ km and stays", "Higher", "Jump ignored"],
            ["Counter resets / new device starts low", "Lower", "Keeps the real km"],
            ["Silent gap with untrustworthy readings either side", "Includes the km", "Lower: those km are lost"],
          ]}
        />
        <p>On Full range with clean readings the two are almost equal.</p>

        <h3>
          <ChartLink to="/#kpi-cards">KPI cards</ChartLink>
        </h3>
        <p>
          The cards follow the page (all customers, buses or trucks) and only count vehicles that drove in the range.{" "}
          <b>Distance / Day</b> and <b>Operating Time / Day</b> are fleet totals ÷ days in the range; the ⚡ and ⏱️
          lines are per-vehicle averages. <b>Peak Active</b> is the vehicle with the most active days (ties: higher Avg
          km/day); <b>Top Customer Account</b> is the customer with the most km.
        </p>

        <h3>Customers page</h3>
        <p>
          <ChartLink to="/#key-customer-accounts">Key Customer Accounts</ChartLink>: one card per customer, most km
          first. Per-vehicle figures are averages across the customer's vehicles that drove. Total Fleet Dist leaves out
          gap distance, so it can sit slightly below the KPI card's Under Operation.
        </p>
        <p>
          <ChartLink to="/#uptime-calendar">Vehicle Uptime Calendar</ChartLink>: did each vehicle run each day? On days
          with no report, the odometer either side of the gap decides. Hover a cell for the reason.
        </p>
        <GTable
          head={["Cell", "Meaning"]}
          rows={[
            ["No colour", "Reported driving."],
            [
              "Orange",
              "No report, but the odometer moved: across a 1-day gap it counts as ran; across a longer gap it is \"not sure\" and does not.",
            ],
            ["Red", "Did not run: reported 0 km, odometer unchanged, or no report since."],
            ["Grey", "Not onboarded yet; left out of Uptime %."],
          ]}
        />
        <p>
          <b>Uptime %</b> = days ran ÷ days onboarded in the range.
        </p>
        <ul>
          <li>
            <ChartLink to="/#daily-distance-trajectory">Daily Distance Trajectory</ChartLink>: each customer's Avg
            km/day per vehicle. <ChartLink to="/#fleet-distance-share">Fleet Distance Share</ChartLink>: each
            customer's share of total km.
          </li>
          <li>
            <ChartLink to="/#dispatch-variance">Dispatch Variance</ChartLink>: box plot of all active vehicle-days per
            customer. The box is the middle half, the line the median, dots are unusual days. Taller box = less steady
            schedule.
          </li>
          <li>
            <ChartLink to="/#day-of-week-rhythm">Day-of-Week Operational Rhythm</ChartLink>: average km of an active
            vehicle-day per weekday, with ±1σ error bars.
          </li>
          <li>
            <ChartLink to="/#soh-odometer">Battery State of Health vs Latest Odometer</ChartLink>: one dot per
            vehicle from the ~08:30 IST morning reading (● bus, ◆ truck). Hollow = don't trust it: a flat 100% default,
            or a live odometer far below the vehicle's history (likely a reset).
          </li>
          <li>
            <ChartLink to="/#active-timeline">Daily Active Commercial Vehicle % Timeline</ChartLink>: share of each
            fleet that drove each day. The base is 10 buses for FreshBus and ZingBus; for trucks, every vehicle
            onboarded by that date.
          </li>
          <li>
            <ChartLink to="/#distance-compartments">Daily Distance Compartments</ChartLink>: each cell is the share of
            that day's vehicles in a distance band (0 km days included). Click a cell to list them in the{" "}
            <ChartLink to="/#duty-inspector">Duty Inspector</ChartLink>.
          </li>
          <li>
            <ChartLink to="/#vehicle-trajectory">Vehicle Distance Trajectory (by SoC)</ChartLink>: daily km for five
            vehicles spread from lowest to highest km/SoC. A break in a line = no report; a point at 0 = reported, didn't
            move.
          </li>
        </ul>
        <GTable
          head={["Distance bands", "Buses (and \"All\")", "Trucks"]}
          rows={[
            ["Bands", "0–200, 201–400, 401–600, 601–800, 800+ km", "0–150, 151–300, 301–450, 450+ km"],
            ["Long-haul", "Over 600 km", "Over 450 km"],
            ["Mid-haul", "200–600 km", "150–450 km"],
          ]}
        />

        <h3>Buses and Trucks pages</h3>
        <p>
          <b>Top 5 / Bottom 5</b> (<ChartLink to="/buses#top-5">buses</ChartLink>,{" "}
          <ChartLink to="/trucks#top-5">trucks</ChartLink>): highest and lowest Avg km/day; the dashed line is the
          page average. <b>The vehicle table</b> (<ChartLink to="/buses#vehicle-table">buses</ChartLink>,{" "}
          <ChartLink to="/trucks#vehicle-table">trucks</ChartLink>) lists vehicles that drove, least active first.
          Click a header to sort. Less obvious columns:
        </p>
        <ul>
          <li>
            <b>Odometer</b> is the raw latest reading, so a device fault can show here.
          </li>
          <li>
            <b>Daily KM Volatility</b>: hover for the tier and σ in km.
          </li>
          <li>
            <b>30-Day Trend</b> covers the selected dates, latest on the left; days with no report are drawn as 0.
          </li>
        </ul>
      </Section>

      <Section id="data-quality" title="Data quality">
        <p>
          Data for yesterday lands around 09:00 IST (Fleetx pull at 08:30, odometer repair at 09:00); today is never
          shown. A day's odometer is treated as faulty if a reading is the stuck value, negative or above 15 lakh km, or
          if it goes backwards or implies 1,500+ km in a day. Faulty days are repaired as follows:
        </p>
        <GTable
          head={["Method", "How the km are worked out", "Trust"]}
          rows={[
            ["Valid reading", "Straight from the odometer.", "High"],
            [
              "Interpolated",
              "The odometer change between good days either side: all of it for one bad day (High), split by reported trip distance across several (Medium).",
              "High / Medium",
            ],
            ["Reported distance", "No usable odometer either side, so the device's own trip distance is used.", "Low"],
            ["Manual override / Unresolved", "Entered by hand (always wins) / nothing usable, no distance.", "— / None"],
          ]}
        />
      </Section>
    </>
  )
}
