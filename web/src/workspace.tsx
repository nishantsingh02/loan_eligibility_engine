import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Workspace, Job, Dashboard, Run, Policy, Request } from "./types";
import { API, errors } from "./lib";
const Context = createContext<Workspace>(null!);
export const useWorkspace = () => useContext(Context);
const storageKey = "clickpe-active-upload";
function savedJob(): Job | null {
  try {
    const j = JSON.parse(
      sessionStorage.getItem(storageKey) || "null",
    ) as Job | null;
    return j?.upload_id && j.phase !== "upload" ? j : null;
  } catch {
    return null;
  }
}
export function Provider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState(
    () => sessionStorage.getItem("clickpe-operator-token") || "",
  );
  const [loaded, setLoaded] = useState(false);
  const [authError, setAuthError] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [runs, setRuns] = useState<Run[]>([]),
    [totalRuns, setTotalRuns] = useState(0);
  const [pending, setPending] = useState<Policy[]>([]),
    [error, setError] = useState("");
  const [job, setJob] = useState<Job | null>(savedJob);
  const operationLock = useRef(false);
  const [operationBusy, setOperationBusy] = useState(false);
  const setToken = useCallback((v: string) => {
    setTokenState(v);
    if (v) sessionStorage.setItem("clickpe-operator-token", v);
    else sessionStorage.removeItem("clickpe-operator-token");
  }, []);
  const expire = useCallback(() => {
    setToken("");
    setAuthError(
      "Your session expired. Sign in again to continue monitoring this file.",
    );
  }, [setToken]);
  const request: Request = useCallback(
    async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      const response = await fetch(`${API}${path}`, {
        ...init,
        headers: {
          "X-Operator-Token": token,
          ...(!(init.body instanceof FormData)
            ? { "Content-Type": "application/json" }
            : {}),
          ...init.headers,
        },
      });
      if (response.status === 401) {
        expire();
        throw new Error("Session expired");
      }
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${response.status})`);
      }
      return response.json() as Promise<T>;
    },
    [token, expire],
  );
  const refresh = useCallback(async () => {
    try {
      const [d, r, p] = await Promise.all([
        request<Dashboard>("/api/dashboard"),
        request<{ items: Run[]; total: number }>("/api/runs?limit=20"),
        request<Policy[]>("/api/policies/pending"),
      ]);
      setLoaded(true);
      setDashboard(d);
      setRuns((current) => [
        ...r.items,
        ...current.filter(
          (old) => !r.items.some((item) => item.run_id === old.run_id),
        ),
      ]);
      setTotalRuns(r.total);
      setPending(p);
      setError("");
    } catch (e) {
      if ((e as Error).message !== "Session expired")
        setError(
          "Unable to refresh the workspace. Check your connection and try again.",
        );
    }
  }, [request]);
  useEffect(() => {
    if (!token) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 6000);
    return () => clearInterval(timer);
  }, [token, refresh]);
  useEffect(() => {
    if (job?.upload_id) sessionStorage.setItem(storageKey, JSON.stringify(job));
    else sessionStorage.removeItem(storageKey);
  }, [job]);
  const active = job?.phase === "import" || job?.phase === "match";
  useEffect(() => {
    if (!token || !active || !job?.upload_id) return;
    let stopped = false,
      timer = 0,
      failures = 0;
    const controller = new AbortController(),
      uploadId = job.upload_id;
    const poll = async () => {
      try {
        const upload = await request<{
          status: string;
          run_id: string | null;
          error_code: string | null;
        }>(`/api/uploads/${uploadId}`, { signal: controller.signal });
        const run = upload.run_id
          ? await request<Run>(`/api/runs/${upload.run_id}/progress`, {
              signal: controller.signal,
            })
          : undefined;
        if (stopped) return;
        failures = 0;
        const failed = upload.status === "failed" || run?.status === "failed";
        const code = run?.error_code || upload.error_code || "";
        setJob(
          (old) =>
            old && {
              ...old,
              run_id: upload.run_id || undefined,
              run,
              reconnecting: false,
              phase: failed
                ? "failed"
                : run?.status === "completed"
                  ? "ready"
                  : run
                    ? "match"
                    : "import",
              error: failed
                ? errors[code] ||
                  "Processing could not finish. Review this run or upload a corrected file."
                : undefined,
            },
        );
        if (failed || run?.status === "completed") {
          void refresh();
          return;
        }
      } catch (e) {
        if (stopped || (e as Error).message === "Session expired") return;
        failures++;
        setJob((old) => old && { ...old, reconnecting: true });
      }
      if (!stopped)
        timer = window.setTimeout(poll, Math.min(1500 * 2 ** failures, 15000));
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [token, active, job?.upload_id, request, refresh]);
  const sendFile = (url: string, body: FormData, authenticated: boolean) =>
    new Promise<{ upload_id?: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      if (authenticated) xhr.setRequestHeader("X-Operator-Token", token);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable)
          setJob(
            (old) =>
              old && {
                ...old,
                percent: Math.min(
                  100,
                  Math.round((event.loaded / event.total) * 100),
                ),
                confirming: event.loaded === event.total,
              },
          );
      };
      xhr.upload.onload = () =>
        setJob((old) => old && { ...old, percent: 100, confirming: true });
      xhr.onload = () => {
        if (xhr.status === 401) {
          expire();
          reject(new Error("Session expired"));
          return;
        }
        let response: { detail?: string; upload_id?: string } = {};
        try {
          response = JSON.parse(xhr.responseText || "{}");
        } catch {
          /* S3 can respond without JSON. */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(response);
        else
          reject(
            new Error(
              response.detail ||
                `Upload was rejected (${xhr.status}). Check the file and try again.`,
            ),
          );
      };
      xhr.onerror = () =>
        reject(
          new Error(
            "The connection ended before upload was confirmed. Check recent files before trying again.",
          ),
        );
      xhr.send(body);
    });
  const upload = async (file: File) => {
    if (operationLock.current || active) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setError("Choose a CSV file.");
      return;
    }
    if (!dashboard) {
      setError("Wait for the workspace to load before uploading.");
      return;
    }
    if (!file.size || file.size > dashboard.max_upload_bytes) {
      setError(
        `Choose a nonempty CSV under ${Math.round(dashboard.max_upload_bytes / 1024 / 1024)} MiB.`,
      );
      return;
    }
    operationLock.current = true;
    setOperationBusy(true);
    setError("");
    setJob({
      filename: file.name,
      started: Date.now(),
      phase: "upload",
      percent: 0,
    });
    try {
      let uploadId: string;
      if (dashboard.storage_mode === "s3") {
        const signed = await request<{
          upload_id: string;
          upload: { url: string; fields: Record<string, string> };
        }>(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {
          method: "POST",
        });
        const body = new FormData();
        Object.entries(signed.upload.fields).forEach(([key, value]) =>
          body.append(key, value),
        );
        body.append("file", file);
        await sendFile(signed.upload.url, body, false);
        uploadId = signed.upload_id;
      } else {
        const body = new FormData();
        body.append("file", file);
        const response = await sendFile(`${API}/api/uploads/local`, body, true);
        if (!response.upload_id)
          throw new Error(
            "Upload confirmation was incomplete. Check recent files before trying again.",
          );
        uploadId = response.upload_id;
      }
      setJob(
        (old) =>
          old && {
            ...old,
            upload_id: uploadId,
            phase: "import",
            percent: 100,
            confirming: false,
          },
      );
    } catch (e) {
      setJob(
        (old) =>
          old && { ...old, phase: "failed", error: (e as Error).message },
      );
    } finally {
      operationLock.current = false;
      setOperationBusy(false);
    }
  };
  const retry = async (runId: string) => {
    if (operationLock.current || active)
      throw new Error(
        "Wait for the current file to finish before retrying another analysis.",
      );
    operationLock.current = true;
    setOperationBusy(true);
    try {
      await request(`/api/runs/${runId}/retry`, { method: "POST" });
      const run = await request<Run>(`/api/runs/${runId}/progress`);
      setJob({
        filename: run.filename,
        started: Date.now(),
        upload_id: run.upload_id,
        run_id: runId,
        phase: "match",
        percent: 100,
        run,
      });
      await refresh();
    } finally {
      operationLock.current = false;
      setOperationBusy(false);
    }
  };
  const moreRuns = async () => {
    const r = await request<{ items: Run[]; total: number }>(
      `/api/runs?limit=20&offset=${runs.length}`,
    );
    setRuns((old) => [
      ...old,
      ...r.items.filter((item) => !old.some((r) => r.run_id === item.run_id)),
    ]);
    setTotalRuns(r.total);
  };
  return (
    <Context.Provider
      value={{
        busy: operationBusy || active,
        loaded,
        token,
        setToken,
        authError,
        dashboard,
        runs,
        totalRuns,
        pending,
        error,
        request,
        refresh,
        job,
        upload,
        clearJob: () => setJob(null),
        retry,
        moreRuns,
      }}
    >
      {children}
    </Context.Provider>
  );
}
