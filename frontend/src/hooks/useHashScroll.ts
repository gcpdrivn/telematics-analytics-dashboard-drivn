import { useEffect } from "react"
import { useLocation } from "react-router-dom"

const WAIT_MS = 15000
const POLL_MS = 150

/** Scrolls to the element named by the URL hash (e.g. /#soh-odometer) and
 * briefly highlights it. React Router doesn't do this itself, and a chart
 * panel only exists once its page's data has loaded, so this polls for the
 * element instead of looking once. */
export function useHashScroll() {
  const { pathname, hash } = useLocation()

  useEffect(() => {
    if (!hash) {
      window.scrollTo(0, 0)
      return
    }
    const id = decodeURIComponent(hash.slice(1))
    const started = Date.now()
    const timer = window.setInterval(() => {
      const el = document.getElementById(id)
      if (el) {
        window.clearInterval(timer)
        el.scrollIntoView({ behavior: "smooth", block: "start" })
        el.classList.remove("anchor-flash")
        void el.offsetWidth // restart the animation on a repeat visit
        el.classList.add("anchor-flash")
      } else if (Date.now() - started > WAIT_MS) {
        window.clearInterval(timer)
      }
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [pathname, hash])
}
