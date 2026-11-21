import { useEffect, useRef, useState } from "react";
import { api, Job } from "./api";

/** Live job list: initial fetch + updates from /api/ws/events (reconnects if the engine restarts). */
export function useJobs(onFinished?: (job: Job) => void) {
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [connected, setConnected] = useState(false);
  const cb = useRef(onFinished);
  cb.current = onFinished;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: number | undefined;

    const connect = () => {
      api.jobs().then((list) => setJobs(Object.fromEntries(list.map((j) => [j.id, j])))).catch(() => {});
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws/events`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) retry = window.setTimeout(connect, 1500);
      };
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type !== "job") return;
        const job: Job = msg.job;
        setJobs((prev) => ({ ...prev, [job.id]: job }));
        if (["done", "failed", "cancelled"].includes(job.status)) cb.current?.(job);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      ws?.close();
    };
  }, []);

  return { jobs, connected };
}
