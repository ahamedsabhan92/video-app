import { useEffect, useState } from "react";
import { api, ModelStatus } from "./api";

export type Models = Partial<Record<ModelStatus["name"], ModelStatus>>;

/** Model readiness; polls every 5 s while anything is downloading/missing, else every 60 s. */
export function useModels(): Models {
  const [models, setModels] = useState<Models>({});
  useEffect(() => {
    let timer: number | undefined;
    let stopped = false;
    const tick = async () => {
      let next = 60_000;
      try {
        const list = await api.models();
        if (!stopped) setModels(Object.fromEntries(list.map((m) => [m.name, m])));
        if (list.some((m) => m.state !== "ready")) next = 5_000;
      } catch { next = 5_000; }
      if (!stopped) timer = window.setTimeout(tick, next);
    };
    tick();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, []);
  return models;
}

/** Tooltip/disabled reason when a capability isn't ready (undefined = ready or unknown yet). */
export function notReady(models: Models, name: ModelStatus["name"]): string | undefined {
  const m = models[name];
  return m && m.state !== "ready" ? m.message : undefined;
}
