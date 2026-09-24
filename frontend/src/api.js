// Thin client for the Audit Monitor API. Base URL comes from VITE_API_BASE
// (empty = same origin, which is how the single-container deployment works).
const BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(`${method} ${path} failed: ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  bootstrap: () => Promise.all([
    request('GET', '/api/kris'), request('GET', '/api/runs'), request('GET', '/api/exceptions'),
    request('GET', '/api/planner'), request('GET', '/api/settings'), request('GET', '/api/planner/reference'),
  ]).then(([kris, runs, excs, planner, settings, reference]) => ({ kris, runs, excs, planner, settings, reference })),

  saveKri: (kri) => request('PUT', `/api/kris/${encodeURIComponent(kri.id)}`, kri),
  setKriStatus: (id, status) => request('PATCH', `/api/kris/${encodeURIComponent(id)}/status`, { status }),
  exportConfig: () => request('GET', '/api/kris/export/config'),

  startRun: (req) => request('POST', '/api/runs', req),
  getRunTrace: (runId) => request('GET', `/api/runs/${encodeURIComponent(runId)}/trace`),

  updateException: (id, patch) => request('PATCH', `/api/exceptions/${encodeURIComponent(id)}`, patch),
  sendToPlanner: (id) => request('POST', `/api/exceptions/${encodeURIComponent(id)}/planner`),

  createPlannerItem: (draft) => request('POST', '/api/planner', draft),
  draftPlan: (id, policy) => request('POST', `/api/planner/${encodeURIComponent(id)}/draft`, { policy }),
  updatePlannerItem: (id, patch) => request('PATCH', `/api/planner/${encodeURIComponent(id)}`, patch),

  saveCalendar: (calendar) => request('PUT', '/api/settings/calendar', calendar),
  reset: () => request('POST', '/api/settings/reset'),
};
