import { DashboardPage } from "../features/dashboard/index";
import { listings, seedKpi } from "../models/fixtures";

if (typeof DashboardPage !== "function") throw new Error("DashboardPage is not a function");
if (listings.length < 8) throw new Error(`Expected 8+ listings, got ${listings.length}`);
if (typeof seedKpi.entriesChecked !== "number") throw new Error("seedKpi.entriesChecked is not a number");
