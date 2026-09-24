import React, { useState, useEffect } from 'react';
import { api } from '../api.js';
import { fmtDate, fmtMoney } from '../constants.js';
import { Btn, Tag, Field } from '../ui.jsx';

export function AuditLogs({ runs, kris, kriById, focusRunId, setFocusRunId, setView, setFocusExc, setRunReq, notify }) {
  const [selectedRunId, setSelectedRunId] = useState(focusRunId || (runs.length > 0 ? runs[0].id : ''));
  const [traceData, setTraceData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('trace'); // 'trace' | 'events' | 'raw'
  const [selectedStepIdx, setSelectedStepIdx] = useState(0);
  const [eventFilter, setEventFilter] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  // Keep selectedRunId synced if focusRunId changes externally
  useEffect(() => {
    if (focusRunId && focusRunId !== selectedRunId) {
      setSelectedRunId(focusRunId);
    }
  }, [focusRunId]);

  // Load trace data whenever selectedRunId changes
  useEffect(() => {
    if (!selectedRunId) return;
    setLoading(true);
    setError(null);
    api.getRunTrace(selectedRunId)
      .then((data) => {
        setTraceData(data);
        setSelectedStepIdx(0);
      })
      .catch((err) => {
        setError(err.message || 'Failed to load trace data');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [selectedRunId]);

  const currentRun = runs.find((r) => r.id === selectedRunId) || (traceData ? traceData.run : null);
  const matchedKri = currentRun ? kriById(currentRun.kriId) : null;
  const steps = traceData?.steps || [];
  const events = traceData?.events || [];
  const rawLog = traceData?.rawLog || '';
  const activeStep = steps[selectedStepIdx] || null;

  const copyToClipboard = (text, msg = 'Copied to clipboard') => {
    navigator.clipboard.writeText(typeof text === 'string' ? text : JSON.stringify(text, null, 2))
      .then(() => notify(msg))
      .catch(() => notify('Failed to copy'));
  };

  const filteredEvents = events.filter((ev) => {
    const matchesStage = eventFilter === 'ALL' || ev.stage === eventFilter;
    const matchesSearch = !searchQuery || JSON.stringify(ev).toLowerCase().includes(searchQuery.toLowerCase());
    return matchesStage && matchesSearch;
  });

  return (
    <section>
      <header className="page-head">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <h1>Audit Trace & Execution Logs</h1>
            <p className="lede">
              Interactive governance traceback showing exactly what tool was called at each step, input arguments, output payloads, and runtime telemetry.
            </p>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            {currentRun && (
              <>
                <Btn kind="default" onClick={() => copyToClipboard(traceData, 'Trace JSON copied')}>
                  Copy Trace JSON
                </Btn>
                {currentRun.exceptions > 0 && (
                  <Btn kind="primary" onClick={() => { setFocusExc(null); setView('exceptions'); }}>
                    View Exceptions ({currentRun.exceptions})
                  </Btn>
                )}
                {matchedKri && (
                  <Btn kind="default" onClick={() => setRunReq({ kriId: matchedKri.id })}>
                    Re-run KRI
                  </Btn>
                )}
              </>
            )}
          </div>
        </div>
      </header>

      {/* Run Selector & Summary Banner */}
      <div className="trace-banner">
        <div className="trace-selector">
          <label style={{ fontSize: '11px', color: 'var(--muted)', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
            SELECT AUDIT RUN
          </label>
          <select
            value={selectedRunId}
            onChange={(e) => {
              setSelectedRunId(e.target.value);
              if (setFocusRunId) setFocusRunId(e.target.value);
            }}
            style={{ fontWeight: 600 }}
          >
            {runs.map((r) => {
              const k = kriById(r.kriId);
              return (
                <option key={r.id} value={r.id}>
                  {r.id} · {k ? k.name : r.kriId} ({r.date} · {r.exceptions} exc)
                </option>
              );
            })}
            {!runs.length && <option value="">No runs recorded</option>}
          </select>
        </div>

        {currentRun && (
          <div className="trace-stats-grid">
            <div>
              <div className="muted small">KRI Target</div>
              <strong>{matchedKri ? matchedKri.name : currentRun.kriId}</strong>
            </div>
            <div>
              <div className="muted small">Status</div>
              <Tag tone={currentRun.status === 'completed' || currentRun.status === 'COMPLETED' ? 'ok' : 'warn'}>
                {String(currentRun.status).toUpperCase()}
              </Tag>
            </div>
            <div>
              <div className="muted small">Audited Period</div>
              <span>{currentRun.period || '2026-01-01 to 2026-03-31'}</span>
            </div>
            <div>
              <div className="muted small">Transactions / Exceptions</div>
              <span>{currentRun.tested || 0} tested · <strong style={{ color: currentRun.exceptions > 0 ? 'var(--bad)' : 'inherit' }}>{currentRun.exceptions || 0} exceptions</strong></span>
            </div>
            <div>
              <div className="muted small">Runtime Duration</div>
              <span>{currentRun.duration || '3.8s'}</span>
            </div>
          </div>
        )}
      </div>

      {/* View Mode Tabs */}
      <div className="filters" style={{ marginTop: '18px', marginBottom: '16px' }}>
        <button className={activeTab === 'trace' ? 'on' : ''} onClick={() => setActiveTab('trace')}>
          ⚡ Step-by-Step Tool Trace ({steps.length} steps)
        </button>
        <button className={activeTab === 'events' ? 'on' : ''} onClick={() => setActiveTab('events')}>
          📜 Chronological Event Log ({events.length} events)
        </button>
        <button className={activeTab === 'raw' ? 'on' : ''} onClick={() => setActiveTab('raw')}>
          🖥️ Raw Console Log (.log)
        </button>
      </div>

      {loading && <p className="muted" style={{ padding: '24px 0' }}>Loading trace data for {selectedRunId}…</p>}
      {error && <div className="callout" style={{ color: 'var(--bad)' }}>Error: {error}</div>}

      {/* TAB 1: INTERACTIVE STEP-BY-STEP TOOL TRACE */}
      {!loading && !error && activeTab === 'trace' && (
        <div className="split trace-split">
          {/* Left: Step Sequence Timeline */}
          <div className="trace-steps-list">
            <h3 style={{ margin: '0 0 10px', fontSize: '13px', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Execution Steps Sequence
            </h3>
            <ul className="step-timeline">
              {steps.map((st, idx) => (
                <li
                  key={idx}
                  className={`step-timeline-item ${idx === selectedStepIdx ? 'active' : ''}`}
                  onClick={() => setSelectedStepIdx(idx)}
                >
                  <div className="step-badge">{st.stepNumber || idx + 1}</div>
                  <div className="step-item-content">
                    <div className="step-item-header">
                      <span className="tool-name-chip">{st.toolName}</span>
                      <span className="step-time">{st.durationMs ? `${st.durationMs.toFixed(1)}ms` : '—'}</span>
                    </div>
                    <div className="step-summary-text">{st.summary}</div>
                    <div className="step-item-footer">
                      <Tag tone={st.status === 'SUCCESS' ? 'ok' : 'bad'}>{st.status}</Tag>
                      {st.timestamp && <span className="muted small">{st.timestamp.split(' ')[1] || st.timestamp}</span>}
                    </div>
                  </div>
                </li>
              ))}
              {!steps.length && (
                <li className="empty" style={{ padding: '24px 14px', color: 'var(--muted)' }}>
                  No tool invocations recorded for this run.
                </li>
              )}
            </ul>
          </div>

          {/* Right: Step Inspector Detail */}
          <div className="detail trace-detail">
            {activeStep ? (
              <div>
                <div className="detail-head" style={{ marginBottom: '14px', borderBottom: '1px solid var(--line)', paddingBottom: '12px' }}>
                  <div>
                    <div className="muted small">
                      Step {activeStep.stepNumber || selectedStepIdx + 1} of {steps.length} · {activeStep.timestamp || 'Recorded during run'}
                    </div>
                    <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '4px' }}>
                      <code>{activeStep.toolName}</code>
                      <Tag tone={activeStep.status === 'SUCCESS' ? 'ok' : 'bad'}>{activeStep.status}</Tag>
                      {activeStep.durationMs > 0 && <span className="muted small" style={{ fontWeight: 400 }}>· {activeStep.durationMs.toFixed(2)} ms</span>}
                    </h2>
                  </div>
                  <Btn kind="default" onClick={() => copyToClipboard(activeStep, `Step ${activeStep.stepNumber} data copied`)}>
                    Copy Step Data
                  </Btn>
                </div>

                {/* Summary Box */}
                <div style={{ background: 'var(--surface)', borderLeft: '3px solid var(--accent)', padding: '12px 16px', borderRadius: '0 6px 6px 0', marginBottom: '18px' }}>
                  <div style={{ fontSize: '11px', color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: '2px' }}>Step Objective & Summary</div>
                  <div style={{ fontSize: '14px', fontWeight: 500 }}>{activeStep.summary}</div>
                </div>

                {/* Error Box if any */}
                {activeStep.error && (
                  <div className="callout" style={{ background: 'var(--bad-soft)', borderColor: 'var(--bad)', color: 'var(--bad)', marginBottom: '16px' }}>
                    <strong>Execution Error:</strong> {activeStep.error}
                  </div>
                )}

                {/* Tool Input Arguments */}
                <div style={{ marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <h3 style={{ margin: 0 }}>Input Arguments (Tool Parameters)</h3>
                    <button className="btn-link" onClick={() => copyToClipboard(activeStep.inputPayload, 'Input JSON copied')}>Copy JSON</button>
                  </div>
                  <pre className="code" style={{ margin: 0, maxHeight: '220px' }}>
                    {JSON.stringify(activeStep.inputPayload || {}, null, 2)}
                  </pre>
                </div>

                {/* Tool Output Result */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <h3 style={{ margin: 0 }}>Output Results (Execution Output)</h3>
                    <button className="btn-link" onClick={() => copyToClipboard(activeStep.outputPayload, 'Output JSON copied')}>Copy JSON</button>
                  </div>
                  <pre className="code" style={{ margin: 0, maxHeight: '320px' }}>
                    {JSON.stringify(activeStep.outputPayload || {}, null, 2)}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="empty" style={{ color: 'var(--muted)', textAlign: 'center', padding: '40px 0' }}>
                Select a step on the left to inspect its parameters and outputs.
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: CHRONOLOGICAL EVENT STREAM */}
      {!loading && !error && activeTab === 'events' && (
        <div>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '14px', flexWrap: 'wrap' }}>
            <Field label="Filter by Stage">
              <select value={eventFilter} onChange={(e) => setEventFilter(e.target.value)} style={{ width: '220px' }}>
                {['ALL', 'TOOL_EXECUTION', 'EXCEPTION_FLAGGED', 'METRICS_CALCULATED', 'EVIDENCE_BUILT', 'PLAN_LOADED', 'RUN_INITIALIZATION', 'RUN_COMPLETED', 'LLM_RESPONSE'].map((st) => (
                  <option key={st} value={st}>{st}</option>
                ))}
              </select>
            </Field>
            <Field label="Search log messages">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search order ID, tool name, text..."
                style={{ width: '280px' }}
              />
            </Field>
          </div>

          <table className="tbl compact" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th style={{ width: '180px' }}>Timestamp</th>
                <th style={{ width: '80px' }}>Step</th>
                <th style={{ width: '180px' }}>Stage</th>
                <th>Message & Payload</th>
              </tr>
            </thead>
            <tbody>
              {filteredEvents.map((ev, i) => (
                <tr key={i}>
                  <td className="muted mono small">{ev.timestamp || '—'}</td>
                  <td><span className="mono">{ev.stepNumber !== undefined ? `#${ev.stepNumber}` : '—'}</span></td>
                  <td>
                    <Tag tone={ev.stage.includes('ERROR') ? 'bad' : ev.stage.includes('TOOL') ? 'info' : ev.stage.includes('EXCEPTION') ? 'warn' : 'neutral'}>
                      {ev.stage}
                    </Tag>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{ev.message}</div>
                    {ev.payload && Object.keys(ev.payload).length > 0 && (
                      <details style={{ marginTop: '4px' }}>
                        <summary style={{ cursor: 'pointer', fontSize: '11px', color: 'var(--accent)' }}>View event payload</summary>
                        <pre className="code" style={{ margin: '4px 0 0', maxHeight: '180px', fontSize: '11px' }}>
                          {JSON.stringify(ev.payload, null, 2)}
                        </pre>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
              {!filteredEvents.length && (
                <tr>
                  <td colSpan="4" className="muted" style={{ textAlign: 'center', padding: '24px 0' }}>
                    No events match the selected stage filter or search query.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* TAB 3: RAW CONSOLE LOG */}
      {!loading && !error && activeTab === 'raw' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <span className="muted small">Source: {currentRun?.logFile || `logs/runs/${selectedRunId}.log`}</span>
            <Btn kind="default" onClick={() => copyToClipboard(rawLog, 'Full raw log copied')}>
              Copy Full Log
            </Btn>
          </div>
          <pre className="code" style={{ maxHeight: 'calc(100vh - 280px)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontFamily: '"Courier New", Courier, monospace', fontSize: '12px', lineHeight: 1.45 }}>
            {rawLog || 'No log output recorded for this run.'}
          </pre>
        </div>
      )}
    </section>
  );
}
