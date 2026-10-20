import { useState } from "react";
import { ArrowRight, Banknote, Check, ShieldCheck } from "lucide-react";
import { useWorkspace } from "../workspace";
import { Heading, InitialState } from "../components";
import { date } from "../lib";
export default function Policies() {
  const { loaded, error, dashboard, pending, request, refresh } =
      useWorkspace(),
    [busy, setBusy] = useState(""),
    [message, setMessage] = useState("");
  if (!loaded)
    return (
      <>
        <Heading title="Policies">
          Review the lender criteria used for new analyses.
        </Heading>
        <InitialState error={error} />
      </>
    );
  return (
    <>
      <Heading title="Policies">
        Review the lender criteria used for new applicant analyses.
      </Heading>
      {message && (
        <p className="notice" role="status">
          {message}
        </p>
      )}
      <div className="section-title">
        <div>
          <h2>Active catalogue</h2>
          <p>Each analysis keeps the policy versions active when it starts.</p>
        </div>
        <span className="count">
          {dashboard?.products.length || 0} products
        </span>
      </div>
      <section className="policy-grid">
        {dashboard?.products.map((p) => (
          <article className="panel policy-card" key={p.product_id}>
            <div className="lender-icon">
              <Banknote size={22} />
            </div>
            <h3>{p.lender}</h3>
            <p>{p.product_name}</p>
            <div className="rate">
              <span>Advertised rate</span>
              <strong>
                {p.advertised_rate || "Not published in this source"}
              </strong>
            </div>
            <p className="small muted">
              {p.extraction_status === "approved"
                ? `Approved source${p.observed_at ? ` · observed ${date(p.observed_at)}` : ""}`
                : "Source requires review"}
            </p>
            <a href={p.product_url} target="_blank" rel="noreferrer">
              Official eligibility source
              <ArrowRight size={14} />
            </a>
          </article>
        ))}
      </section>
      <div className="section-title policy-review-title">
        <div>
          <h2>Changes awaiting approval</h2>
          <p>
            Review the source and proposed criteria before applying a new policy
            to future analyses.
          </p>
        </div>
        <span className="count">{pending.length} pending</span>
      </div>
      {pending.length ? (
        pending.map((p) => (
          <article className="panel pending-policy" key={p.snapshot_id}>
            <div className="section-title">
              <div>
                <h3>
                  {p.lender} · {p.product_name}
                </h3>
                <p>Source observed {date(p.observed_at)}</p>
              </div>
              <span className="status review">Needs approval</span>
            </div>
            {p.advertised_rate && (
              <p className="rate">Advertised rate: {p.advertised_rate}</p>
            )}
            <dl className="policy-rules">
              {Object.entries(p.rules)
                .filter(
                  ([key]) =>
                    !["version", "incomplete_eligibility"].includes(key),
                )
                .map(([key, rule]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>
                      {rule.kind === "unknown"
                        ? "Not specified — requires review"
                        : `${rule.status || rule.min || ""}${rule.max ? `–${rule.max}` : ""}${rule.currency ? ` ${rule.currency}` : ""}${rule.kind === "preferred" ? " preferred" : ""}${rule.location_dependent ? " · varies by location" : ""}`}
                    </dd>
                  </div>
                ))}
            </dl>
            <details className="source">
              <summary>Review source evidence</summary>
              {p.rate_source_quote && (
                <blockquote>{p.rate_source_quote}</blockquote>
              )}
              <pre>{p.source_excerpt}</pre>
              <a href={p.source_url} target="_blank" rel="noreferrer">
                Open official source
                <ArrowRight size={14} />
              </a>
              {p.reviewer_note && (
                <details className="audit">
                  <summary>Extraction notes</summary>
                  <p>{p.reviewer_note}</p>
                </details>
              )}
            </details>
            <div className="approval">
              <p>Approval applies to future analyses.</p>
              <button
                className="button primary"
                disabled={!!busy}
                onClick={async () => {
                  setBusy(p.snapshot_id);
                  setMessage("");
                  try {
                    await request(`/api/policies/${p.snapshot_id}/approve`, {
                      method: "POST",
                      body: "{}",
                    });
                    setMessage(
                      `${p.lender} policy approved. New analyses will use this version.`,
                    );
                    await refresh();
                  } catch (e) {
                    setMessage((e as Error).message);
                  } finally {
                    setBusy("");
                  }
                }}
              >
                {busy === p.snapshot_id ? "Approving…" : "Approve criteria"}
                <Check size={16} />
              </button>
            </div>
          </article>
        ))
      ) : (
        <div className="panel empty">
          <ShieldCheck />
          <h3>No policy changes awaiting approval</h3>
          <p>New source changes will appear here for your review.</p>
        </div>
      )}
    </>
  );
}
