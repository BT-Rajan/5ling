import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";

export type ServiceState = "checking" | "up" | "down";
export type DatabaseState = "checking" | "ok" | "needs_setup" | "unavailable" | "unknown";

export interface Health {
  service: ServiceState;
  database: DatabaseState;
}

interface ReadyBody {
  checks?: { database?: string };
}

function fromDatabaseCheck(value: string | undefined): DatabaseState {
  if (value === "ok") return "ok";
  if (value === "not_migrated") return "needs_setup";
  return "unavailable";
}

export function useHealth(): Health & { refresh: () => void } {
  const [health, setHealth] = useState<Health>({ service: "checking", database: "checking" });
  const [round, setRound] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api<ReadyBody>("/api/health/ready")
      .then((body) => {
        if (!cancelled) setHealth({ service: "up", database: fromDatabaseCheck(body.checks?.database) });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 503) {
          const body = error.body as ReadyBody | null;
          setHealth({ service: "up", database: fromDatabaseCheck(body?.checks?.database) });
        } else {
          setHealth({ service: "down", database: "unknown" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [round]);

  const refresh = useCallback(() => {
    setHealth({ service: "checking", database: "checking" });
    setRound((n) => n + 1);
  }, []);
  return { ...health, refresh };
}
