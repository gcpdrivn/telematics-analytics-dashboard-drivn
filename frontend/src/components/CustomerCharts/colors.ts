import type { CustomerName } from "../../api/types"

// Single source of truth for the frontend's customer roster -- keep in sync
// with backend/metrics.py's _CUSTOMER_REGISTRY.
export const ALL_CUSTOMERS: CustomerName[] = [
  "FreshBus", "ZingBus", "BillionE", "AVG LOGISTICS", "SWITCHLABS",
  "Purple Mobility", "Enviiirowheels", "Flytta", "Juleh",
]

export const BUS_CUSTOMERS: ReadonlySet<CustomerName> = new Set([
  "FreshBus", "ZingBus", "Purple Mobility", "Juleh",
])

export const TRUCK_CUSTOMERS: ReadonlySet<CustomerName> = new Set([
  "BillionE", "AVG LOGISTICS", "SWITCHLABS", "Enviiirowheels", "Flytta",
])

export const CUSTOMER_COLORS: Record<CustomerName, string> = {
  FreshBus: "#5cb030",
  ZingBus: "#0284c7",
  BillionE: "#d97706",
  "AVG LOGISTICS": "#9333ea",
  SWITCHLABS: "#dc2626",
  "Purple Mobility": "#8b5cf6",
  Enviiirowheels: "#059669",
  Flytta: "#ea580c",
  Juleh: "#db2777",
}

export const PLOT_LAYOUT_BASE = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  margin: { l: 60, r: 20, t: 20, b: 40 },
}
