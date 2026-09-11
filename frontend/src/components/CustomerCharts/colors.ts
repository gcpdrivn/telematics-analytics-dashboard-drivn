import type { CustomerName } from "../../api/types"

export const CUSTOMER_COLORS: Record<CustomerName, string> = {
  FreshBus: "#5cb030",
  ZingBus: "#0284c7",
  BillionE: "#d97706",
}

export const PLOT_LAYOUT_BASE = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  margin: { l: 60, r: 20, t: 20, b: 40 },
}
