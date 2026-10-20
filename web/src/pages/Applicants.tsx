import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowRight, LoaderCircle, Users } from "lucide-react";
import { useWorkspace } from "../workspace";
import { Heading, Status, InitialState } from "../components";
import { fmt, date, labels, errors } from "../lib";
import type { Run, Applicant } from "../types";
import ApplicantDrawer from "../components/ApplicantDrawer";
import ApplicantPagination from "../components/ApplicantPagination";
function useRun(runId: string, revision = 0) {
  const { request } = useWorkspace(),
    [run, setRun] = useState<Run | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    setRun(null);
    setError("");
    if (!runId) return;
    const controller = new AbortController();
    let timer = 0;
    const poll = async () => {
      try {
        const data = await request<Run>(`/api/runs/${runId}/progress`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setRun(data);
        setError("");
        if (!["completed", "failed"].includes(data.status))
          timer = window.setTimeout(poll, 3000);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError((e as Error).message);
          timer = window.setTimeout(poll, 6000);
        }
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, request, revision]);
  return { run, error };
}
export default function Applicants() {
  const {
      busy: workspaceBusy,
      loaded,
      error: workspaceError,
      runs,
      totalRuns,
      moreRuns,
      request,
      retry,
      dashboard,
    } = useWorkspace(),
    [params, setParams] = useSearchParams();
  const runId = params.get("run") || runs[0]?.run_id || "",
    status = params.get("status") || "",
    product = params.get("product") || "",
    q = params.get("q") || "";
  const size = [25, 50, 100].includes(Number(params.get("size")))
    ? Number(params.get("size"))
    : 25;
  const page = Math.max(
    1,
    Math.min(
      Math.ceil(100000 / size),
      Math.floor(Number(params.get("page"))) || 1,
    ),
  );
  const [revision, setRevision] = useState(0);
  const { run, error: runError } = useRun(runId, revision);
  const [data, setData] = useState<{
      items: Applicant[];
      total: number;
    } | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
      eligible_matches: number;
      distinct_email_addresses: number;
      run_id: string;
    } | null>(null),
    [busy, setBusy] = useState(false);
  const update = (changes: Record<string, string>, reset = true) => {
    const next = new URLSearchParams(params);
    next.set("run", runId);
    if (reset) next.delete("page");
    Object.entries(changes).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    setParams(next);
  };
  useEffect(() => {
    if (!params.get("run") && runId) {
      const next = new URLSearchParams(params);
      next.set("run", runId);
      setParams(next, { replace: true });
    }
  }, [params, runId, setParams]);
  useEffect(() => {
    setPreview(null);
    setSelected(null);
  }, [runId]);
  useEffect(() => {
    setData(null);
    setError("");
    if (!runId) return;
    const controller = new AbortController();
    setLoading(true);
    const query = new URLSearchParams({
      limit: String(size),
      offset: String((page - 1) * size),
    });
    if (status) query.set("status", status);
    if (product) query.set("matched_product_id", product);
    if (q) query.set("q", q);
    const timer = window.setTimeout(
      () => {
        void request<{ items: Applicant[]; total: number }>(
          `/api/runs/${runId}/applicants?${query}`,
          { signal: controller.signal },
        )
          .then((result) => {
            if (controller.signal.aborted) return;
            const last = Math.max(1, Math.ceil(result.total / size));
            if (page > last) {
              const next = new URLSearchParams(params);
              next.set("page", String(last));
              setParams(next, { replace: true });
              return;
            }
            setData(result);
          })
          .catch((e) => {
            if (!controller.signal.aborted) setError((e as Error).message);
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
      },
      q ? 250 : 0,
    );
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [
    request,
    runId,
    status,
    product,
    q,
    size,
    page,
    run?.processed_decisions,
    params,
    setParams,
  ]);
  const products = [
    ...new Map(
      (data?.items.flatMap((item) => item.matched_products) || []).map(
        (item) => [item.product_id, item],
      ),
    ).values(),
  ];
  if (!loaded)
    return (
      <>
        <Heading title="Applicants">
          Review applicant matches and lender decisions.
        </Heading>
        <InitialState error={workspaceError} />
      </>
    );
  return (
    <>
      <Heading title="Applicants">
        Review each applicant’s matched products and every lender decision.
      </Heading>
      <section className="panel run-picker">
        <label htmlFor="run">Analysis file</label>
        <div className="inline">
          <select
            id="run"
            value={runId}
            onChange={(e) =>
              update({ run: e.target.value, status: "", product: "", q: "" })
            }
          >
            {!runId && <option value="">No analyses available</option>}
            {runId && !runs.some((r) => r.run_id === runId) && (
              <option value={runId}>
                {run?.filename || "Selected analysis"}
              </option>
            )}
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.filename} · {date(r.created_at)}
              </option>
            ))}
          </select>
          {runs.length < totalRuns && (
            <button
              className="button outline"
              onClick={() =>
                void moreRuns().catch((e) => setError((e as Error).message))
              }
            >
              Load older files
            </button>
          )}
          {run && <Status value={run.status} />}
        </div>
      </section>
      {!runId ? (
        <div className="panel empty">
          <Users />
          <h2>Import your first applicant file</h2>
          <p>Completed and in-progress analyses will be available here.</p>
          <Link className="button primary" to="/overview">
            Import applicants
          </Link>
        </div>
      ) : (
        <>
          {runError && (
            <p className="alert" role="alert">
              {runError}
            </p>
          )}
          {run && (
            <section
              className="metrics applicant-metrics"
              aria-label="Applicant summary"
            >
              {["matched", "review", "unmatched", "processing"].map((key) => (
                <article className="metric" key={key}>
                  <span>{labels[key]}</span>
                  <strong>{fmt(run.applicant_summary?.[key] || 0)}</strong>
                </article>
              ))}
            </section>
          )}
          {run && !["completed", "failed"].includes(run.status) && (
            <div className="notice" role="status">
              <LoaderCircle size={18} className="spinner" />
              <span>
                Matching in progress · {fmt(run.processed_decisions || 0)} of{" "}
                {fmt(run.row_count * (run.product_count || 0))} lender decisions
                saved. Results update automatically.
              </span>
            </div>
          )}
          {run?.status === "failed" && (
            <div className="alert">
              <span>
                {errors[run.error_code || ""] ||
                  "This analysis could not finish. Retry matching to resume."}
              </span>
              <button
                disabled={busy || workspaceBusy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await retry(runId);
                    setRevision((n) => n + 1);
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Retry matching
              </button>
            </div>
          )}
          <section className="panel applicants-panel">
            <div className="filters">
              <label>
                Applicant reference
                <input
                  type="search"
                  placeholder="Search by ID prefix"
                  value={q}
                  maxLength={128}
                  onChange={(e) => update({ q: e.target.value })}
                />
              </label>
              <label>
                Status
                <select
                  value={status}
                  onChange={(e) => update({ status: e.target.value })}
                >
                  <option value="">All applicants</option>
                  {["matched", "review", "unmatched", "processing"].map(
                    (key) => (
                      <option key={key} value={key}>
                        {labels[key]}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label>
                Matched lender product
                <select
                  value={product}
                  onChange={(e) => update({ product: e.target.value })}
                >
                  <option value="">All products</option>
                  {[
                    ...new Map(
                      [
                        ...(run?.products || dashboard?.products || []),
                        ...products,
                      ].map((p) => [p.product_id, p]),
                    ).values(),
                  ].map((p) => (
                    <option key={p.product_id} value={p.product_id}>
                      {p.lender} · {p.product_name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {error && (
              <p className="alert" role="alert">
                {error}
              </p>
            )}
            <div className="table-wrap applicant-table" aria-busy={loading}>
              <table>
                <thead>
                  <tr>
                    <th>Applicant reference</th>
                    <th>Matched lender products</th>
                    <th>Status</th>
                    <th>
                      <span className="sr-only">Details</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {loading
                    ? Array.from({ length: 8 }, (_, i) => (
                        <tr key={i}>
                          <td>
                            <span className="skeleton" />
                          </td>
                          <td>
                            <span className="skeleton wide" />
                          </td>
                          <td>
                            <span className="skeleton" />
                          </td>
                          <td />
                        </tr>
                      ))
                    : data?.items.map((item) => (
                        <tr key={item.user_id}>
                          <td>
                            <strong>{item.user_id}</strong>
                          </td>
                          <td>
                            <div className="chips">
                              {item.matched_products.map((p) => (
                                <span key={p.product_id} title={p.product_name}>
                                  {p.lender} · {p.product_name}
                                </span>
                              ))}
                              {!item.matched_products.length && (
                                <span className="muted">
                                  {item.status === "processing"
                                    ? "Awaiting completed checks"
                                    : "No eligible products"}
                                </span>
                              )}
                            </div>
                          </td>
                          <td>
                            <Status value={item.status} />
                          </td>
                          <td>
                            <button
                              className="row-link"
                              aria-label={`View decisions for ${item.user_id}`}
                              onClick={() => setSelected(item.user_id)}
                            >
                              View decisions
                              <ArrowRight size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                </tbody>
              </table>
              {!loading && data?.items.length === 0 && (
                <div className="empty">
                  <Users />
                  <h3>No applicants match these filters</h3>
                  <p>Try another reference, status or lender product.</p>
                  <button
                    className="button outline"
                    onClick={() => update({ q: "", status: "", product: "" })}
                  >
                    Clear filters
                  </button>
                </div>
              )}
            </div>
            <ApplicantPagination
              loading={loading}
              page={page}
              size={size}
              total={data?.total || 0}
              update={update}
            />
          </section>
          {run?.status === "completed" && (
            <section className="panel notification">
              <div>
                <h2>Notification preview</h2>
                <p>
                  Preview the eligible matches and distinct applicant email
                  addresses for this file.
                </p>
                {preview?.run_id === runId && (
                  <p role="status">
                    <strong>
                      {fmt(preview.eligible_matches)} eligible matches
                    </strong>{" "}
                    across {fmt(preview.distinct_email_addresses)} email
                    addresses.
                  </p>
                )}
              </div>
              <button
                className="button outline"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const result = await request<{
                      eligible_matches: number;
                      distinct_email_addresses: number;
                    }>(`/api/runs/${runId}/notification-preview`, {
                      method: "POST",
                      body: "{}",
                    });
                    setPreview({ ...result, run_id: runId });
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Preview notifications
              </button>
            </section>
          )}
          <NotificationSummary key={runId} runId={runId} />
        </>
      )}
      {selected && (
        <ApplicantDrawer
          runId={runId}
          userId={selected}
          close={() => setSelected(null)}
        />
      )}
    </>
  );
}
function NotificationSummary({ runId }: { runId: string }) {
  const { request } = useWorkspace(),
    [counts, setCounts] = useState<Record<string, number> | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    const c = new AbortController();
    const load = () =>
      void request<Record<string, number>>(`/api/runs/${runId}/notifications`, {
        signal: c.signal,
      })
        .then((d) => {
          if (!c.signal.aborted) {
            setCounts(d);
            setError("");
          }
        })
        .catch(() => {
          if (!c.signal.aborted) {
            setCounts(null);
            setError("Notification status unavailable. Reconnecting?");
          }
        });
    load();
    const t = setInterval(load, 6000);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, [request, runId]);
  if (error)
    return (
      <section
        className="notification-summary"
        aria-label="Notification status"
      >
        <strong>Notifications</strong>
        <span>{error}</span>
      </section>
    );
  return (
    counts && (
      <section
        className="notification-summary"
        aria-label="Notification status"
      >
        <strong>Notifications</strong>
        <span>Preview ready: {fmt(counts.preview_ready)}</span>
        <span>Sent: {fmt(counts.sent)}</span>
        <span>Pending: {fmt(counts.pending)}</span>
        <span>Needs attention: {fmt(counts.needs_attention)}</span>
      </section>
    )
  );
}
