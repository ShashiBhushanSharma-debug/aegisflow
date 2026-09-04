const BASE = "/api/v1";

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }

  if (!res.ok) {
    const detail = body && body.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail
        ? JSON.stringify(detail)
        : `${res.status} ${res.statusText}`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return body;
}

export const listPending = () => request("/cases/pending");

export const getCase = (caseId) => request(`/cases/${encodeURIComponent(caseId)}`);

export const approveCase = (caseId, payload) =>
  request(`/cases/${encodeURIComponent(caseId)}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const rejectCase = (caseId, payload) =>
  request(`/cases/${encodeURIComponent(caseId)}/reject`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const submitCase = (caseId) =>
  request(`/cases/${encodeURIComponent(caseId)}/submit`, { method: "POST" });
