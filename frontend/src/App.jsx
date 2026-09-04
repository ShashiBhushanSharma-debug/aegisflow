import { useCallback, useEffect, useRef, useState } from "react";
import Queue from "./components/Queue.jsx";
import CaseDetail from "./components/CaseDetail.jsx";
import { money } from "./verdict.js";
import {
  listPending,
  listHistory,
  getStats,
  getCase,
  approveCase,
  rejectCase,
  submitCase,
} from "./api.js";

const POLL_MS = 5_000;

export default function App() {
  const [tab, setTab] = useState("review");
  const [pending, setPending] = useState([]);
  const [history, setHistory] = useState([]);
  const [stats, setStats] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [queueError, setQueueError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [newIds, setNewIds] = useState(new Set());

  const selectedRef = useRef(null);
  const seenRef = useRef(new Set());
  const firstLoadRef = useRef(true);

  selectedRef.current = selectedId;

  const refresh = useCallback(async () => {
    try {
      const [p, h, s] = await Promise.all([
        listPending(),
        listHistory().catch(() => ({ cases: [] })),
        getStats().catch(() => null),
      ]);

      const rows = p?.pending_cases || [];

      // Flag cases that appeared since the last poll, so arrivals are visible
      // on screen rather than silently changing the list length.
      if (firstLoadRef.current) {
        rows.forEach((r) => seenRef.current.add(r.case_id));
        firstLoadRef.current = false;
      } else {
        const fresh = rows
          .map((r) => r.case_id)
          .filter((id) => !seenRef.current.has(id));
        if (fresh.length) {
          fresh.forEach((id) => seenRef.current.add(id));
          setNewIds((prev) => new Set([...prev, ...fresh]));
          setTimeout(
            () =>
              setNewIds((prev) => {
                const next = new Set(prev);
                fresh.forEach((id) => next.delete(id));
                return next;
              }),
            6000
          );
        }
      }

      setPending(rows);
      setHistory(h?.cases || []);
      if (s) setStats(s);
      setQueueError(null);
    } catch (err) {
      setQueueError(
        `Cannot reach the API — ${err.message}. Is the backend running on port 8000?`
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshDetail = useCallback(async (caseId) => {
    if (!caseId) return null;
    try {
      const data = await getCase(caseId);
      if (selectedRef.current === caseId) setDetail(data);
      return data;
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (selectedId) refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  const select = (caseId) => {
    setNotice(null);
    setDetail(null);
    setSelectedId(caseId);
  };

  // Approval hands off to Celery, so status changes a moment later.
  const settle = useCallback(
    async (caseId, expected) => {
      for (let i = 0; i < 10; i += 1) {
        await new Promise((r) => setTimeout(r, 1200));
        const data = await refreshDetail(caseId);
        const status = data?.case?.status;
        if (status && (!expected || expected.includes(status))) return status;
      }
      return null;
    },
    [refreshDetail]
  );

  const handleApprove = async (payload) => {
    setBusy(true);
    setNotice(null);
    try {
      await approveCase(selectedId, payload);
      setNotice({ kind: "ok", text: "Approved. Staging at Razorpay…" });
      const status = await settle(selectedId, ["DRAFTED", "BLOCKED", "SUBMIT_FAILED"]);
      if (status === "DRAFTED") {
        setNotice({ kind: "ok", text: "Staged at Razorpay. Review it, then file." });
      } else if (status) {
        setNotice({ kind: "error", text: `Case is now ${status}.` });
      }
      refresh();
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async (payload) => {
    setBusy(true);
    setNotice(null);
    try {
      await rejectCase(selectedId, payload);
      setNotice({ kind: "ok", text: "Rejected." });
      await refreshDetail(selectedId);
      refresh();
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async () => {
    setBusy(true);
    setNotice(null);
    try {
      await submitCase(selectedId);
      setNotice({ kind: "ok", text: "Filing with Razorpay…" });
      const status = await settle(selectedId, [
        "SUBMITTED",
        "CONCEDED",
        "SUBMIT_FAILED",
        "BLOCKED",
      ]);
      if (status === "SUBMITTED") {
        setNotice({ kind: "ok", text: "Filed. Razorpay is reviewing the evidence." });
      } else if (status === "CONCEDED") {
        setNotice({ kind: "ok", text: "Dispute accepted. The case is closed." });
      } else if (status) {
        setNotice({ kind: "error", text: `Case is now ${status}.` });
      }
      refresh();
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">AegisFlow</span>
        </div>
        <p className="topbar-context">Chargeback review</p>

        {stats && (
          <div className="topstats">
            <span><b>{stats.awaiting_review}</b> awaiting</span>
            <span><b>{money(stats.amount_recovered)}</b> recovered</span>
            <span><b>{money(stats.predicted_recovery)}</b> predicted</span>
            <span><b>{money(stats.amount_conceded)}</b> conceded</span>
          </div>
        )}
      </header>

      <main className="layout">
        <Queue
          tab={tab}
          onTab={setTab}
          pending={pending}
          history={history}
          selectedId={selectedId}
          onSelect={select}
          loading={loading}
          error={queueError}
          newIds={newIds}
        />
        <CaseDetail
          detail={detail}
          busy={busy}
          notice={notice}
          onApprove={handleApprove}
          onReject={handleReject}
          onSubmit={handleSubmit}
        />
      </main>
    </div>
  );
}
