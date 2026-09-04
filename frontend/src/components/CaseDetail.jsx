import { useEffect, useState } from "react";
import { verdict, isConcession, money, timeLeft, when, newest } from "../verdict.js";

const SIGNAL_LABELS = {
  status: "Tracking status",
  delivered: "Delivered",
  failed_delivery: "Delivery failed",
  pod: "Proof of delivery",
  fraud_score: "Fraud score",
  low_fraud: "Low fraud risk",
  no_evidence: "No evidence found",
};

function signalValue(value) {
  if (value === true) return "Yes";
  if (value === false) return "No";
  if (value === null || value === undefined) return "—";
  return String(value);
}

function Signals({ signals }) {
  if (!signals) return null;
  const keys = Object.keys(SIGNAL_LABELS).filter((k) => k in signals);
  if (keys.length === 0) return null;

  return (
    <dl className="signals">
      {keys.map((key) => (
        <div key={key} className="signal">
          <dt>{SIGNAL_LABELS[key]}</dt>
          <dd className={signals[key] === true ? "is-yes" : signals[key] === false ? "is-no" : ""}>
            {signalValue(signals[key])}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function CaseDetail({ detail, onApprove, onReject, onSubmit, busy, notice }) {
  const record = detail?.case;
  const claim = newest(detail?.claims);
  const decision = newest(detail?.policy_decisions);
  const run = newest(detail?.agent_runs);

  const [narrative, setNarrative] = useState("");
  const [reviewer, setReviewer] = useState(
    () => localStorage.getItem("aegisflow.reviewer") || ""
  );
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    setNarrative(claim?.statement || "");
    setRejecting(false);
    setReason("");
  }, [claim?.claim_id, claim?.statement]);

  useEffect(() => {
    if (reviewer) localStorage.setItem("aegisflow.reviewer", reviewer);
  }, [reviewer]);

  if (!record) {
    return (
      <section className="detail detail-empty">
        <p>Pick a case from the queue to review the evidence and the draft.</p>
      </section>
    );
  }

  const recovery = run?.output_payload?.recovery || {};
  const triage = run?.output_payload?.triage || decision?.rationale || {};
  const logs = run?.output_payload?.logs || [];
  const v = verdict(decision?.action);
  const conceding = isConcession(decision?.action);
  const left = timeLeft(record.deadline);
  const prob = Number(decision?.win_probability ?? 0);

  const canReview = record.status === "HUMAN_REVIEW";
  const canFile = record.status === "DRAFTED";
  const edited = narrative.trim() !== (claim?.statement || "").trim();
  const blocked = claim?.is_grounded === false;

  const approveDisabled =
    busy || !reviewer.trim() || narrative.trim().length < 50 || (blocked && !edited);

  const approveLabel = conceding
    ? "Approve concession and stage it"
    : "Approve and stage draft";
  const fileLabel = conceding ? "Concede at Razorpay" : "File with Razorpay";

  return (
    <section className="detail">
      <header className="detail-head">
        <div className="detail-head-main">
          <p className="detail-order">{record.order_id}</p>
          <h2 className="detail-amount">{money(record.amount, record.currency)}</h2>
          <p className="detail-meta">
            {record.reason_code} · {record.status.toLowerCase().replace(/_/g, " ")}
          </p>
        </div>
        <div className="detail-head-side">
          <span className={`verdict-chip tone-${v.tone} is-large`}>{v.label}</span>
          {left && (
            <span className={`detail-clock${left.urgent ? " is-urgent" : ""}`}>
              {left.text} · respond by {when(record.deadline)}
            </span>
          )}
        </div>
      </header>

      {recovery.overridden && (
        <div className="override" role="alert">
          <p className="override-title">Recommendation changed by the evidence check</p>
          <p className="override-body">{recovery.override_reason}</p>
        </div>
      )}

      {blocked && (
        <div className="warn" role="alert">
          <p>
            The draft failed its automated checks. Edit it below before approving —
            approving it unchanged will be refused.
          </p>
        </div>
      )}

      {notice && (
        <div className={`notice is-${notice.kind}`} role="status">
          {notice.text}
        </div>
      )}

      <div className="detail-grid">
        <section className="card">
          <h3>What the model concluded</h3>
          <p className="reasoning">{triage.reasoning || "No reasoning recorded."}</p>
          <dl className="stats">
            <div>
              <dt>Recovery odds</dt>
              <dd className="stat-figure">{Math.round(prob * 100)}%</dd>
            </div>
            <div>
              <dt>Expected recovery</dt>
              <dd className="stat-figure">
                {money(decision?.expected_recovery_value, record.currency)}
              </dd>
            </div>
            <div>
              <dt>Dispute category</dt>
              <dd>{triage.category || "—"}</dd>
            </div>
            <div>
              <dt>Risk to merchant</dt>
              <dd>{triage.risk_level || "—"}</dd>
            </div>
          </dl>
        </section>

        <section className="card">
          <h3>Evidence signals</h3>
          <Signals signals={recovery.signals} />
          <p className="card-foot">
            {(detail?.evidence || []).length} evidence records ·{" "}
            {run?.latency_ms ? `${run.latency_ms} ms` : "—"} to process
          </p>
        </section>
      </div>

      <section className="card">
        <h3>How it got here</h3>
        <ol className="log">
          {logs.length === 0 && <li>No execution log recorded.</li>}
          {logs.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ol>
      </section>

      <section className="card">
        <h3>{conceding ? "Concession rationale" : "Evidence summary sent to Razorpay"}</h3>
        <textarea
          className="narrative"
          value={narrative}
          onChange={(e) => setNarrative(e.target.value)}
          rows={conceding ? 4 : 16}
          spellCheck="false"
          readOnly={!canReview}
          aria-label="Evidence summary"
        />
        <p className="card-foot">
          {narrative.trim().split(/\s+/).filter(Boolean).length} words
          {edited && canReview && " · edited, will be re-checked on approval"}
        </p>
      </section>

      {canReview && (
        <section className="actions">
          <label className="field">
            <span>Your name</span>
            <input
              type="text"
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              placeholder="Who is signing off"
            />
          </label>

          <div className="action-row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={approveDisabled}
              onClick={() => onApprove({ reviewer, approved_narrative: narrative })}
            >
              {busy ? "Working…" : approveLabel}
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              disabled={busy}
              onClick={() => setRejecting((r) => !r)}
            >
              {rejecting ? "Cancel" : "Reject"}
            </button>
          </div>

          {conceding && (
            <p className="action-note">
              Approving accepts the dispute. The full{" "}
              {money(record.amount, record.currency)} stays with the customer.
            </p>
          )}

          {rejecting && (
            <div className="reject">
              <label className="field">
                <span>Why are you rejecting this?</span>
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={3}
                  placeholder="At least 10 characters. This is stored on the case."
                />
              </label>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy || !reviewer.trim() || reason.trim().length < 10}
                onClick={() =>
                  onReject({ reviewer, rejection_reason: reason })
                }
              >
                Reject this case
              </button>
            </div>
          )}
        </section>
      )}

      {canFile && (
        <section className="actions">
          <p className="action-note">
            Staged at Razorpay. Check it in their dashboard, then file it — this
            cannot be undone.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={onSubmit}
          >
            {busy ? "Working…" : fileLabel}
          </button>
        </section>
      )}

      {!canReview && !canFile && (
        <section className="actions">
          <p className="action-note">
            This case is {record.status.toLowerCase().replace(/_/g, " ")}. No action
            available.
            {record.error ? ` Last error: ${record.error}` : ""}
          </p>
        </section>
      )}
    </section>
  );
}
