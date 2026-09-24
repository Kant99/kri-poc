import React, { useState, useEffect } from 'react';
import { api } from './api.js';
import { fmtDate, TODAY, CLOSE_CALENDAR } from './constants.js';
import { Overview } from './pages/Overview.jsx';
import { KriLibrary } from './pages/KriLibrary.jsx';
import { Schedule, RunModal } from './pages/Schedule.jsx';
import { Exceptions } from './pages/Exceptions.jsx';
import { AuditLogs } from './pages/AuditLogs.jsx';
import { Planner } from './pages/Planner.jsx';
import { Settings } from './pages/Settings.jsx';

const NAV = [
  ['overview', 'Overview'], ['kris', 'KRI library'], ['schedule', 'Schedule & runs'], ['exceptions', 'Exceptions'], ['logs', 'Audit trace & logs'], ['planner', 'Audit planner'], ['settings', 'Settings'],
];

export default function App() {
  const [view, setView] = useState('overview');
  const [kris, setKris] = useState([]);
  const [runs, setRuns] = useState([]);
  const [excs, setExcs] = useState([]);
  const [planner, setPlanner] = useState([]);
  const [settings, setSettings] = useState(null);
  const [reference, setReference] = useState({ policies: [], priorWork: [] });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [toast, setToast] = useState(null);
  const [runReq, setRunReq] = useState(null); // { kriId }
  const [focusExc, setFocusExc] = useState(null);
  const [focusRunId, setFocusRunId] = useState(null);

  const notify = (msg) => setToast(msg);
  const fail = (e) => notify(e.message || 'Something went wrong');

  const reload = () => api.bootstrap().then((d) => {
    setKris(d.kris); setRuns(d.runs); setExcs(d.excs); setPlanner(d.planner); setSettings(d.settings); setReference(d.reference); setLoadError(null);
  }).catch((e) => setLoadError(e.message)).finally(() => setLoading(false));

  useEffect(() => { reload(); }, []);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 3200); return () => clearTimeout(t); }, [toast]);

  const kriById = (id) => kris.find((k) => k.id === id);
  const replaceIn = (setter, item) => setter((xs) => (xs.some((x) => x.id === item.id) ? xs.map((x) => (x.id === item.id ? item : x)) : [...xs, item]));

  // ----- KRI configuration -----
  const saveKri = (k) => api.saveKri(k).then((saved) => { replaceIn(setKris, saved); notify(`${saved.id} saved`); return saved; }).catch(fail);
  const setKriStatus = (id, status, label) => api.setKriStatus(id, status).then((saved) => { replaceIn(setKris, saved); notify(`${id} ${label}`); }).catch(fail);

  // ----- exceptions -----
  const updateExc = (id, patch) => api.updateException(id, patch).then((saved) => replaceIn(setExcs, saved)).catch(fail);
  const sendToPlanner = (exc) => api.sendToPlanner(exc.id).then((item) => {
    setPlanner((p) => [item, ...p]);
    replaceIn(setExcs, { ...exc, status: 'planner' });
    notify(`${exc.id} sent to the Audit planner as ${item.id}`);
  }).catch(fail);

  // ----- runs -----
  const completeRun = (kri, opts) => api.startRun({ kriId: kri.id, period: opts.period, region: opts.region, bg: opts.bg, sampling: opts.sampling }).then(({ run, exceptions }) => {
    setRuns((r) => [run, ...r]);
    setExcs((x) => [...exceptions, ...x]);
    setKris((ks) => ks.map((k) => (k.id === kri.id ? { ...k, lastRun: TODAY.toISOString().slice(0, 10) } : k)));
    if (exceptions.length) setFocusExc(exceptions[0].id);
    setRunReq(null);
    setView('exceptions');
    notify(`${kri.id} finished: ${run.tested} items tested, ${exceptions.length} exception${exceptions.length === 1 ? '' : 's'}`);
  }).catch((e) => { setRunReq(null); fail(e); });

  // ----- planner -----
  const addPlannerItem = (draft) => api.createPlannerItem(draft).then((item) => { setPlanner((p) => [item, ...p]); notify(`${item.id} added to the planner`); return item; }).catch(fail);
  const updatePlanner = (id, patch, msg) => api.updatePlannerItem(id, patch).then((item) => { replaceIn(setPlanner, item); if (msg) notify(msg); return item; }).catch(fail);
  const draftPlan = (id, policy) => api.draftPlan(id, policy).then((item) => { replaceIn(setPlanner, item); notify(`Plan drafted for ${id}`); return item; }).catch(fail);

  // ----- settings -----
  const saveCalendar = (cal) => api.saveCalendar(cal).then((saved) => { setSettings((s) => ({ ...s, calendar: saved })); notify('Calendar saved – scheduled runs recalculated'); }).catch(fail);
  const resetData = () => api.reset().then(() => { setFocusExc(null); return reload(); }).then(() => notify('Sample data restored')).catch(fail);

  const openExceptions = excs.filter((e) => e.status === 'open');
  const unexplainedOpen = openExceptions.filter((e) => e.classification === 'unexplained');
  const newPlanner = planner.filter((p) => p.status === 'New').length;

  const props = { kris, runs, excs, planner, settings, reference, notify, kriById, setView, setRunReq, focusExc, setFocusExc, focusRunId, setFocusRunId, saveKri, setKriStatus, updateExc, sendToPlanner, addPlannerItem, updatePlanner, draftPlan, saveCalendar, resetData };

  return (
    <div className="app">
      <nav className="rail" aria-label="Sections">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <div><div className="brand-name">Audit monitor</div><div className="brand-sub">Internal Audit · continuous KRI monitoring</div></div>
        </div>
        <ul>
          {NAV.map(([id, label]) => (
            <li key={id}><button className={view === id ? 'on' : ''} onClick={() => setView(id)}>
              {label}
              {id === 'exceptions' && unexplainedOpen.length > 0 && <span className="pill">{unexplainedOpen.length}</span>}
              {id === 'planner' && newPlanner > 0 && <span className="pill">{newPlanner}</span>}
            </button></li>
          ))}
        </ul>
        <div className="rail-foot">
          <div>Admin · Audit Manager</div>
          <div className="muted">Close calendar {settings ? settings.calendar.pattern : CLOSE_CALENDAR.pattern} · next close {fmtDate(CLOSE_CALENDAR.periods.find((p) => new Date(p.close) >= TODAY).close)}</div>
        </div>
      </nav>
      <main className="main">
        {loading && <p className="muted">Loading…</p>}
        {loadError && <div className="callout">The API is not reachable ({loadError}). Start the backend and reload. <button className="btn" onClick={() => { setLoading(true); reload(); }}>Retry</button></div>}
        {!loading && !loadError && view === 'overview' && <Overview {...props} />}
        {!loading && !loadError && view === 'kris' && <KriLibrary {...props} />}
        {!loading && !loadError && view === 'schedule' && <Schedule {...props} />}
        {!loading && !loadError && view === 'exceptions' && <Exceptions {...props} />}
        {!loading && !loadError && view === 'logs' && <AuditLogs {...props} />}
        {!loading && !loadError && view === 'planner' && <Planner {...props} />}
        {!loading && !loadError && view === 'settings' && <Settings {...props} />}
      </main>
      {runReq && kriById(runReq.kriId) && <RunModal kri={kriById(runReq.kriId)} onClose={() => setRunReq(null)} onDone={completeRun} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
