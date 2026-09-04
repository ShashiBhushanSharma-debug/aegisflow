import { verdict, money, timeLeft, newest } from "../verdict.js";

function QueueRow({ row, active, onSelect }) {
  const decision = newest(row.policy_decisions);
  const v = verdict(decision?.action);
  const left = timeLeft(row.deadline);
  const prob = decision?.win_probability;

  return (
    <li>
      <button
        type="button"
        className={`queue-row tone-${v.tone}${active ? " is-active" : ""}`}
        onClick={() => onSelect(row.case_id)}
        aria-current={active ? "true" : undefined}
      >
        <span className="queue-row-rail" aria-hidden="true" />
        <span className="queue-row-head">
          <span className="queue-row-amount">{money(row.amount, row.currency)}</span>
          {left && (
            <span className={`queue-row-clock${left.urgent ? " is-urgent" : ""}`}>
              {left.text}
            </span>
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

export default function Queue({ cases, selectedId, onSelect, loading, error }) {
  return (
    <aside className="queue">
      <header className="queue-head">
        <h2 className="queue-title">Waiting on you</h2>
        <span className="queue-count">{cases.length}</span>
      </header>

      {error && <p className="queue-message is-error">{error}</p>}

      {!error && loading && cases.length === 0 && (
        <p className="queue-message">Loading cases…</p>
      )}

      {!error && !loading && cases.length === 0 && (
        <p className="queue-message">
          Nothing to review. New disputes land here once Razorpay sends them and
          the evidence pipeline finishes.
        </p>
      )}

      <ul className="queue-list">
        {cases.map((row) => (
          <QueueRow
            key={row.case_id}
            row={row}
            active={row.case_id === selectedId}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </aside>
  );
}
