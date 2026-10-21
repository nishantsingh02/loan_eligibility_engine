import { useEffect, useRef, useState } from "react";
import { ArrowRight, X } from "lucide-react";
import { useWorkspace } from "../workspace";
import { Status } from "../components";
import { date } from "../lib";
import type { Applicant, Result } from "../types";
export default function ApplicantDrawer({
  runId,
  userId,
  close,
}: {
  runId: string;
  userId: string;
  close: () => void;
}) {
  const { request } = useWorkspace(),
    dialog = useRef<HTMLDialogElement>(null),
    [data, setData] = useState<(Applicant & { results: Result[] }) | null>(
      null,
    ),
    [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current!,
      previous = document.activeElement as HTMLElement;
    element.showModal();
    return () => {
      element.close();
      previous?.focus();
    };
  }, []);
  useEffect(() => {
    const c = new AbortController();
    void request<Applicant & { results: Result[] }>(
      `/api/runs/${runId}/applicant?user_id=${encodeURIComponent(userId)}`,
      { signal: c.signal },
    )
      .then((d) => {
        if (!c.signal.aborted) setData(d);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError((e as Error).message);
      });
    return () => c.abort();
  }, [request, runId, userId]);
  return (
    <dialog
      ref={dialog}
      className="drawer"
      aria-labelledby="applicant-title"
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === dialog.current) close();
      }}
    >
      <div className="drawer-content">
        <div className="section-title">
          <div>
            <p className="eyebrow">APPLICANT DECISIONS</p>
            <h2 id="applicant-title">{userId}</h2>
          </div>
          <button
            autoFocus
            className="icon-button"
            onClick={close}
            aria-label="Close applicant details"
          >
            <X />
          </button>
        </div>
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        {!data && !error && <p role="status">Loading lender decisions…</p>}
        {data && (
          <>
            <div className="drawer-summary">
              <Status value={data.status} />
              <span>{data.results.length} lender decisions saved</span>
            </div>
            {data.status === "processing" && (
              <p className="notice">
                Some lender checks are still processing. Close and reopen to
                refresh these decisions.
              </p>
            )}
            {data.results.map((result) => (
              <article className="decision-card" key={result.result_id}>
                <div className="section-title">
                  <div>
                    <h3>{result.lender}</h3>
                    <p>{result.product_name}</p>
                  </div>
                  <Status value={result.decision} />
                </div>
                {result.advertised_rate && (
                  <p className="rate">
                    Advertised rate: {result.advertised_rate}
                  </p>
                )}
                <div className="reasons">
                  {result.reasons.map((reason, i) => (
                    <div key={i}>
                      <div>
                        <strong>{reason.criterion.replaceAll("_", " ")}</strong>
                        <span className={`reason-status ${reason.status}`}>
                          {reason.status}
                        </span>
                      </div>
                      <p>
                        {reason.message}
                        {reason.observed_value !== undefined &&
                          ` Observed: ${reason.observed_value}.`}
                        {reason.required_value &&
                          ` Required: ${reason.required_value}.`}
                        {reason.minimum !== undefined &&
                          ` Minimum: ${reason.minimum}${reason.currency ? ` ${reason.currency}` : ""}.`}
                        {reason.maximum !== undefined &&
                          ` Maximum: ${reason.maximum}.`}
                        {reason.preferred_minimum !== undefined &&
                          ` Preferred minimum: ${reason.preferred_minimum}.`}
                      </p>
                    </div>
                  ))}
                </div>
                <a href={result.source_url} target="_blank" rel="noreferrer">
                  View official source
                  <ArrowRight size={14} />
                </a>
                <details className="audit">
                  <summary>Policy and audit details</summary>
                  <dl>
                    <dt>Policy snapshot</dt>
                    <dd>{result.snapshot_id}</dd>
                    <dt>Rule version</dt>
                    <dd>{result.rule_version}</dd>
                    <dt>Source observed</dt>
                    <dd>{date(result.observed_at)}</dd>
                  </dl>
                </details>
              </article>
            ))}
            {!data.results.length && (
              <p>No lender decisions have been saved yet.</p>
            )}
          </>
        )}
      </div>
    </dialog>
  );
}
