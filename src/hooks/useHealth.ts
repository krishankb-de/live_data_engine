import { useEffect, useState } from "react";
import { api } from "../lib/api";

export type HealthState = "unknown" | "online" | "offline";

const POLL_INTERVAL_MS = 15_000;

export function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>("unknown");

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await api.getHealth();
        if (!cancelled) setState(res.ok ? "online" : "offline");
      } catch {
        if (!cancelled) setState("offline");
      }
    };
    void tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return state;
}
