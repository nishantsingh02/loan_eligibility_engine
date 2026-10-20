import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  ArrowRight,
  Check,
  FileText,
  LoaderCircle,
  UploadCloud,
  X,
} from "lucide-react";
import { useWorkspace } from "../workspace";
import { Heading, Status } from "../components";
import { fmt, date } from "../lib";
function Progress() {
  const { job, clearJob, retry, busy: workspaceBusy } = useWorkspace(),
    [now, setNow] = useState(Date.now()),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!job) return null;
  const step = {
    upload: 0,
    import: 1,
    match: 2,
    ready: 3,
    failed: job.run_id ? 2 : job.upload_id ? 1 : 0,
  }[job.phase];
  const total = (job.run?.row_count || 0) * (job.run?.product_count || 0),
    processed = job.run?.processed_decisions || 0;
  const percent =
    job.phase === "upload"
      ? job.percent
      : job.phase === "ready"
        ? 100
        : total
          ? Math.min(100, (processed / total) * 100)
          : 0;
  return (
    <section className="panel progress-panel" aria-label="Upload progress">
      <div className="section-title">
        <div>
          <h2>
            {job.phase === "ready"
              ? "Applicants are ready"
              : job.phase === "failed"
                ? "This file needs attention"
                : "Preparing your applicants"}
          </h2>
          <p className="filename">{job.filename}</p>
        </div>
        {(job.phase === "ready" || job.phase === "failed") && (
          <button
            className="icon-button"
            aria-label="Dismiss upload status"
            onClick={clearJob}
          >
            <X size={18} />
          </button>
        )}
      </div>
      <ol className="steps">
        {["Upload", "Import", "Match", "Ready"].map((label, i) => (
          <li
            key={label}
            className={
              i < step || job.phase === "ready"
                ? "done"
                : i === step
                  ? "current"
                  : ""
            }
          >
            <span>
              {i < step || job.phase === "ready" ? <Check size={14} /> : i + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>
      <div role="status" aria-live="polite" className="progress-copy">
        {job.reconnecting ? (
          <>
            <LoaderCircle className="spinner" size={17} />
            Reconnecting · your file continues processing
          </>
        ) : job.phase === "upload" ? (
          <>
            <LoaderCircle className="spinner" size={17} />
            {job.confirming
              ? "100% sent · confirming receipt…"
              : `Uploading · ${job.percent}%`}
          </>
        ) : job.phase === "import" ? (
          <>
            <LoaderCircle className="spinner" size={17} />
            Importing applicant rows · {Math.floor((now - job.started) / 1000)}s
            elapsed
          </>
        ) : job.phase === "match" ? (
          <>
            <LoaderCircle className="spinner" size={17} />
            {fmt(processed)} of {fmt(total)} lender decisions saved
          </>
        ) : job.phase === "ready" ? (
          `${fmt(job.run?.row_count || 0)} applicants checked against ${job.run?.product_count || 0} lender products`
        ) : (
          job.error
        )}
      </div>
      {(job.phase === "upload" || job.phase === "match") && (
        <progress
          max="100"
          value={percent}
          aria-label={
            job.phase === "upload" ? "File transfer" : "Lender matching"
          }
        />
      )}
      {job.phase === "import" && (
        <p className="muted small">
          You can keep working here. Progress continues across workspace pages.
        </p>
      )}
      {job.phase === "ready" && (
        <Link className="button primary" to={`/applicants?run=${job.run_id}`}>
          View applicants
          <ArrowRight size={16} />
        </Link>
      )}
      {job.phase === "failed" && job.run_id && (
        <button
          className="button outline"
          disabled={busy || workspaceBusy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await retry(job.run_id!);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Retrying…" : "Retry matching"}
        </button>
      )}
      {error && (
        <p role="alert" className="alert">
          {error}
        </p>
      )}
    </section>
  );
}
export default function Overview() {
  const { dashboard, runs, totalRuns, pending, job, upload, busy } =
      useWorkspace(),
    file = useRef<HTMLInputElement>(null),
    [dragging, setDragging] = useState(false);
  return (
    <>
      <Heading title="Overview">
        Import an applicant file and follow its progress through eligibility
        matching.
      </Heading>
      <section className="metrics" aria-label="Workspace summary">
        {[
          ["Applicants imported", dashboard?.total_rows],
          ["Analysis runs", totalRuns],
          ["Active lender products", dashboard?.products.length],
          ["Policy changes to review", pending.length],
        ].map(([label, value]) => (
          <article className="metric" key={label}>
            <span>{label}</span>
            <strong>{dashboard ? fmt(Number(value)) : "—"}</strong>
          </article>
        ))}
      </section>
      <div className="overview-grid">
        <section className="panel upload-panel">
          <div className="section-title">
            <div>
              <h2>Import applicants</h2>
              <p>Start a new eligibility analysis with a CSV file.</p>
            </div>
            <UploadCloud size={23} />
          </div>
          <button
            className={`dropzone ${dragging ? "dragging" : ""}`}
            disabled={busy || !dashboard}
            onClick={() => file.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (!busy && e.dataTransfer.files[0])
                void upload(e.dataTransfer.files[0]);
            }}
          >
            <UploadCloud size={30} />
            <strong>
              {busy
                ? "Your file is being processed"
                : "Choose a CSV or drop it here"}
            </strong>
            <span>
              Up to{" "}
              {dashboard
                ? Math.round(dashboard.max_upload_bytes / 1024 / 1024)
                : 25}{" "}
              MiB · 100,000 applicants
            </span>
          </button>
          <input
            hidden
            ref={file}
            type="file"
            accept=".csv,text/csv"
            aria-label="Applicant CSV"
            onChange={(e) => {
              const selected = e.target.files?.[0];
              e.target.value = "";
              if (selected) void upload(selected);
            }}
          />
          <details className="csv-help">
            <summary>Required CSV columns</summary>
            <code>
              user_id, name, email, monthly_income, credit_score,
              employment_status, age
            </code>
            <p>
              Use UTF-8. Each applicant needs a unique user_id. Income is
              monthly, in INR.
            </p>
          </details>
        </section>
        {job ? (
          <Progress />
        ) : (
          <section className="panel guide">
            <FileText size={26} />
            <h2>From file to clear next steps</h2>
            <p>
              Each applicant is checked against the lender policies active when
              the analysis starts.
            </p>
            <p>
              Review matched products and the reasons behind every lender
              decision on the Applicants page.
            </p>
            <Link to="/policies">
              View active policies
              <ArrowRight size={15} />
            </Link>
          </section>
        )}
      </div>
      <section className="panel">
        <div className="section-title">
          <div>
            <h2>Recent files</h2>
            <p>Your latest applicant analyses.</p>
          </div>
          <Link to="/applicants">
            Browse applicants
            <ArrowRight size={15} />
          </Link>
        </div>
        {runs.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th>Imported</th>
                  <th>Applicants</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 8).map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <strong>{run.filename}</strong>
                    </td>
                    <td>{date(run.created_at)}</td>
                    <td>{fmt(run.row_count)}</td>
                    <td>
                      <Status value={run.status} />
                    </td>
                    <td>
                      <Link
                        className="row-link"
                        to={`/applicants?run=${run.run_id}`}
                      >
                        Open
                        <ArrowRight size={14} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            <FileText />
            <h3>
              {dashboard ? "No files imported yet" : "Loading recent files…"}
            </h3>
            <p>Your first analysis will appear here after import.</p>
          </div>
        )}
      </section>
    </>
  );
}
