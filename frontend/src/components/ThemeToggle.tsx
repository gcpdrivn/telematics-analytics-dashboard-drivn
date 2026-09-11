import { useEffect, useState } from "react"

type Theme = "light" | "dark"

function getInitialTheme(): Theme {
  try {
    const saved = localStorage.getItem("drivn-theme") || localStorage.getItem("theme")
    if (saved === "light" || saved === "dark") return saved
  } catch {
    // localStorage unavailable (private mode etc.) -- fall through to default
  }
  return "light"
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme)
    try {
      localStorage.setItem("drivn-theme", theme)
      localStorage.setItem("theme", theme)
    } catch {
      // ignore
    }
  }, [theme])

  return (
    <button
      className="btn-action"
      onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
      aria-label="Toggle theme"
    >
      {theme === "dark" ? "☀️" : "🌙"}
    </button>
  )
}
