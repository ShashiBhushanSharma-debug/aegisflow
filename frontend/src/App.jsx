import { useCallback, useEffect, useRef, useState } from "react";
import Queue from "./components/Queue.jsx";
import CaseDetail from "./components/CaseDetail.jsx";
import { listPending, getCase, approveCase, rejectCase, submitCase } from "./api.js";

const POLL_MS = 10_000;

export default function App() {
  const [cases, setCases] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [queueError, setQueueError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const selectedRef = useRef(null);

  selectedRef.current = selectedId;

  const refreshQueue = useCallback(async () => {
    try {
      const data = await listPending();
      setCases(data?.pending_cases || []);
      setQueueError(null);
    } catch (err) {
      setQueueError(
        `Cannot reach the API — ${err.message}. Is the backend running on port 8000?`
      );
    } finally {
      setLoadingQueue(false);
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
    refreshQueue();
    const id = setInterval(refreshQueue, POLL_MS);
    return () => clearInterval(id);
  }, [refreshQueue]);

  useEffect(() => {
    if (selectedId) refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  const select = (caseId) => {
    setNotice(null);
    setDetail(null);
    setSelectedId(caseId);
  };

  // Approval hands off to Celery, so the case status changes a moment later.
  // Poll briefly rather than showing a stale screen.
  const settle = useCallback(
    async (caseId, expected) => {
      for (let i = 0; i < 8; i += 1) {
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
      setNotice({ kind: "ok", text: "Approved. Staging the draft at Razorpay…" });
      const status = await settle(selectedId, ["DRAFTED", "BLOCKED", "SUBMIT_FAILED"]);
      if (status === "DRAFTED") {
        setNotice({ kind: "ok", text: "Staged at Razorpay. Review it, then file." });
      } else if (status) {
        setNotice({ kind: "error", text: `Case is now ${status}.` });
      }
      refreshQueue();
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
      refreshQueue();
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
      refreshQueue();
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
      </header>

      <main className="layout">
        <Queue
          cases={cases}
          selectedId={selectedId}
          onSelect={select}
          loading={loadingQueue}
          error={queueError}
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
