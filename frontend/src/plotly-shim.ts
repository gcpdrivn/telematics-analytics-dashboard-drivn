// react-plotly.js@2.6.0 / plotly.js's CJS export chain doesn't survive Vite's
// bundler interop cleanly, and different bundler versions have wrapped it
// differently (plain object, single .default, double-wrapped .default.default)
// while iterating on this -- unwrapCJS walks through .default until it finds
// something actually callable, so this keeps working regardless of exactly
// how the dev-dependency optimizer decides to wrap it this time.
function unwrapCJS<T>(mod: unknown): T {
  let m: any = mod
  for (let i = 0; i < 3 && m && typeof m !== "function" && "default" in m; i++) {
    m = m.default
  }
  return m as T
}

import * as PlotlyModule from "plotly.js/dist/plotly"
import * as FactoryModule from "react-plotly.js/factory"

const Plotly = unwrapCJS<any>(PlotlyModule)
const createPlotlyComponent = unwrapCJS<(p: any) => any>(FactoryModule)

const Plot = createPlotlyComponent(Plotly)
export default Plot
