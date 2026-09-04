// The policy_action enum carries two overlapping vocabularies: the model's
// recommendation (FIGHT / ACCEPT / REVIEW) and older system actions. Both map
// onto the three outcomes an operator can act on.
const VERDICTS = {
  FIGHT: { label: "Contest", tone: "fight" },
  AUTO_SUBMIT: { label: "Contest", tone: "fight" },
  AUTO_DRAFT: { label: "Contest", tone: "fight" },
  ACCEPT: { label: "Concede", tone: "accept" },
  ACCEPT_LOSS: { label: "Concede", tone: "accept" },
  REVIEW: { label: "Needs decision", tone: "review" },
  HUMAN_REVIEW: { label: "Needs decision", tone: "review" },
};

const STATUS_LABELS = {
  HUMAN_REVIEW: "Awaiting review",
  DRAFTED: "Staged",
  SUBMITTED: "Filed",
  CONCEDED: "Conceded",
  WON: "Won",
  LOST: "Lost",
  CLOSED: "Closed",
  REJECTED: "Rejected",
  BLOCKED: "Blocked",
  SUBMIT_FAILED: "Filing failed",
  FAILED: "Pipeline failed",
  EVIDENCE_REQUESTED: "Gathering evidence",
  APPROVED: "Approved",
  OPEN: "New",
};

export function verdict(action) {
  return VERDICTS[action] || { label: action || "Unknown", tone: "review" };
}

export function isConcession(action) {
  return verdict(action).tone === "accept";
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || (status || "").toLowerCase().replace(/_/g, " ");
}

export function money(amount, currency = "INR") {
  const value = Number(amount || 0);
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

// Operators care how long they have left, not the absolute timestamp.
export function timeLeft(deadline) {
  if (!deadline) return null;
  const ms = new Date(deadline).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return { text: "Deadline passed", urgent: true };

  const hours = Math.floor(ms / 3_600_000);
  if (hours < 24) return { text: `${hours}h left`, urgent: true };

  const days = Math.floor(hours / 24);
  return { text: `${days}d left`, urgent: days <= 2 };
}

export function when(timestamp) {
  if (!timestamp) return "—";
  const d = new Date(timestamp);
  if (Number.isNaN(d.getTime())) return String(timestamp);
  return d.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Newest first; falls back to array order when rows carry no timestamp.
export function newest(rows) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const sorted = [...rows].sort((a, b) => {
    const at = new Date(a?.created_at || 0).getTime();
    const bt = new Date(b?.created_at || 0).getTime();
    return bt - at;
  });
  return sorted[0];
}
