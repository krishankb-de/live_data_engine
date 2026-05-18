import { useCallback, useEffect, useRef, useState } from "react";
import { api, type BrainMetrics } from "../lib/api";

const POLL_MS = 30_000;

export function useBrainMetrics() {
  const [metrics, setMetrics] = useState<BrainMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetch = useCallback(async () => {
    try {
      const m = await api.getBrainMetrics();
      setMetrics(m);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void fetch();
    timerRef.current = setInterval(() => { void fetch(); }, POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetch]);

  return { metrics, error, refresh: fetch };
}
