import { verdict, money, timeLeft, newest, statusLabel } from "../verdict.js";

function QueueRow({ row, active, onSelect, showStatus, isNew }) {
  const decision = newest(row.policy_decisions);
  const v = verdict(decision?.action);
  const left = timeLeft(row.deadline);
  const prob = decision?.win_probability;

  return (
    <li>
      <button
        type="button"
        className={`queue-row tone-${v.tone}${active ? " is-active" : ""}${
          isNew ? " is-new" : ""
        }`}
        onClick={() => onSelect(row.case_id)}
        aria-current={active ? "true" : undefined}
      >
        <span className="queue-row-rail" aria-hidden="true" />
        <span className="queue-row-head">
          <span className="queue-row-amount">{money(row.amount, row.currency)}</span>
          {showStatus ? (
            <span className="queue-row-clock">{statusLabel(row.status)}</span>
          ) : (
            left && (
              <span className={`queue-row-clock${left.urgent ? " is-urgent" : ""}`}>
                {left.text}
              </span>
            )
          )}
        </span>
        <span className="queue-row-id">{row.order_id}</span>
        <span className="queue-row-foot">
          <span className="verdict-chip">{v.label}</span>
          {prob != null && (
            <span className="queue-row-prob">
              {Math.round(Number(prob) * 100)}% recovery odds
            </span>
          )}
        </span>
      </button>
    </li>
  );
}

export default function Queue({
  tab,
  onTab,
  pending,
  history,
  selectedId,
  onSelect,
  loading,
  error,
  newIds,
}) {
  const rows = tab === "review" ? pending : history;
  const isReview = tab === "review";

  return (
    <aside className="queue">
      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={isReview}
          className={`tab${isReview ? " is-active" : ""}`}
          onClick={() => onTab("review")}
        >
          Waiting on you
          <span className="tab-count">{pending.length}</span>
        </button>
        <button
          role="tab"
          aria-selected={!isReview}
          className={`tab${!isReview ? " is-active" : ""}`}
          onClick={() => onTab("filed")}
        >
          Decided
          <span className="tab-count">{history.length}</span>
        </button>
      </div>

      {error && <p className="queue-message is-error">{error}</p>}

      {!error && loading && rows.length === 0 && (
        <p className="queue-message">Loading cases…</p>
      )}

      {!error && !loading && rows.length === 0 && (
        <p className="queue-message">
          {isReview
            ? "Nothing to review. New disputes land here once Razorpay sends them and the evidence pipeline finishes."
            : "No decisions yet. Cases appear here once you approve or reject them."}
        </p>
      )}

      <ul className="queue-list">
        {rows.map((row) => (
          <QueueRow
            key={row.case_id}
            row={row}
            active={row.case_id === selectedId}
            onSelect={onSelect}
            showStatus={!isReview}
            isNew={isReview && newIds.has(row.case_id)}
          />
        ))}
      </ul>
    </aside>
  );
}
