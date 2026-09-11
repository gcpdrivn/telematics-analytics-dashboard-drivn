// plotly.js/dist/plotly (the browser UMD bundle, used by plotly-shim.ts to
// avoid a CJS interop bug -- see that file) has no declaration file of its
// own; @types/plotly.js only covers the main "plotly.js" entry point.
declare module "plotly.js/dist/plotly" {
  const Plotly: unknown
  export default Plotly
}
