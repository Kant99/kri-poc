import React, { useState, useEffect, useRef } from 'react';
import { fmtDate, addDays, TODAY, CLOSE_CALENDAR, STAGES, srcName, nextRunFor, periodLabelFor } from '../constants.js';
import { Btn, Tag, Field } from '../ui.jsx';
export function Schedule({ kris, runs, setRunReq, kriById, setView, setFocusExc, setFocusRunId }) {
  const active = kris.filter((k) => k.status === 'active');
  const upcoming = active.map((k) => ({ k, d: nextRunFor(k) })).filter((x) => x.d).sort((a, b) => a.d - b.d);
  return (
    <section>
      <header className="page-head"><h1>Schedule & runs</h1><p className="lede">Scheduled runs follow the {CLOSE_CALENDAR.pattern} close calendar. Any active KRI can also be run on demand for a chosen period, region or business group.</p></header>
      <div className="cols">
        <div className="col">
          <h3>Close calendar</h3>
          <table className="tbl compact">
            <thead><tr><th>Period</th><th>Close</th><th>Runs triggered</th></tr></thead>
            <tbody>
              {CLOSE_CALENDAR.periods.filter((p) => new Date(p.close) >= addDays(TODAY, -20)).map((p) => {
                const trig = active.filter((k) => k.frequency === 'monthly' || (k.frequency === 'quarterly' && p.quarterEnd));
                return (<tr key={p.p}><td><strong>{p.p}</strong> {p.label}{p.quarterEnd && <Tag tone="info">{p.q} end</Tag>}</td><td>{fmtDate(p.close)}</td><td className="wrap">{trig.map((k) => `${k.id} (+${k.fetchOffsetDays}d)`).join(', ') || '—'}</td></tr>);
              })}
            </tbody>
          </table>
        </div>
        <div className="col">
          <h3>Upcoming</h3>
          <ul className="list">
            {upcoming.map(({ k, d }) => (
              <li key={k.id} className="list-row"><div><strong>{k.id}</strong> · {k.name}<div className="muted small">{periodLabelFor(k)} · reviewer {k.reviewer}</div></div><div className="right"><div>{fmtDate(d)}</div><Btn kind="link" onClick={() => setRunReq({ kriId: k.id })}>Run now instead</Btn></div></li>
            ))}
          </ul>
        </div>
      </div>
      <h3>Run history</h3>
      <table className="tbl">
        <thead><tr><th>Run</th><th>KRI</th><th>Period</th><th>Trigger</th><th>Started</th><th>Population</th><th>Exceptions</th><th>Actions</th></tr></thead>
        <tbody>
          {runs.map((r) => { const k = kriById(r.kriId); return (
            <tr key={r.id}>
              <td><strong>{r.id}</strong><div className="muted small">{r.duration}</div></td>
              <td>{r.kriId}<div className="muted small">{k ? k.name : ''}</div></td>
              <td>{r.period}</td><td>{r.trigger}</td>
              <td>{fmtDate(r.startedAt)}</td>
              <td>{r.tested.toLocaleString()} <span className="muted">of {r.population.toLocaleString()}</span></td>
              <td>{r.exceptions === 0 ? <Tag tone="ok">none</Tag> : <span><Tag tone="bad">{r.unexplained} unexplained</Tag> <Tag tone="ok">{r.explainable} explainable</Tag></span>}</td>
              <td className="row-actions">
                <Btn kind="link" onClick={() => { if (setFocusRunId) setFocusRunId(r.id); setView('logs'); }}>Trace</Btn>
                {r.exceptions > 0 && <Btn kind="link" onClick={() => { setFocusExc(null); setView('exceptions'); }}>Exceptions</Btn>}
              </td>
            </tr>
          ); })}
        </tbody>
      </table>
    </section>
  );
}


export function RunModal({ kri, onClose, onDone }) {
  const [opts, setOpts] = useState({ period: periodLabelFor(kri), region: 'All regions', bg: 'BG-A + BG-B', sampling: kri.sampling || 100 });
  const [stage, setStage] = useState(-1);
  const running = stage >= 0;
  const timer = useRef(null);

  // Construct dynamic stages directly incorporating the KRI's configured test steps
  const runStages = React.useMemo(() => {
    if (kri.steps && kri.steps.length > 0) {
      const srcList = kri.sources && kri.sources.length > 0 ? kri.sources : ['sap', 'red_box_po'];
      const stages = [
        ['Connecting with the agent service account', srcList[0]],
        ['Extracting the full population for the period', srcList[0]],
      ];
      kri.steps.forEach((stepText, idx) => {
        const src = srcList[Math.min(idx, srcList.length - 1)] || null;
        stages.push([stepText, src]);
      });
      stages.push(['Cross-checking against sources and calculating variances', srcList[Math.min(1, srcList.length - 1)]]);
      stages.push(['Writing explainability reasoning and compiling audit evidence', null]);
      stages.push(['Populating testing report and audit workpapers', 'tickets']);
      return stages;
    }
    return STAGES;
  }, [kri]);

  const start = () => {
    setStage(0);
    let s = 0;
    timer.current = setInterval(() => {
      s += 1;
      setStage(s);
      if (s >= runStages.length) {
        clearInterval(timer.current);
        // The visible stages are the UX; the API executes the run and returns the result.
        setTimeout(() => onDone(kri, opts), 500);
      }
    }, 650);
  };
  useEffect(() => () => clearInterval(timer.current), []);

  return (
    <div className="scrim" onClick={running ? undefined : onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Run KRI now">
        <h2>Run {kri.id} now</h2>
        <p className="muted">{kri.name}</p>
        {!running ? (
          <div>
            <div className="grid2">
              <Field label="Period"><select value={opts.period} onChange={(e) => setOpts({ ...opts, period: e.target.value })}>{['P9 · Sep 2026 (to date)', 'P8 · Aug 2026', 'P7 · Jul 2026', 'Q3 FY26 (to date)', 'Q2 FY26'].map((p) => <option key={p}>{p}</option>)}</select></Field>
              <Field label="Region"><select value={opts.region} onChange={(e) => setOpts({ ...opts, region: e.target.value })}>{['All regions', 'MEA', 'Europe', 'APAC', 'Americas'].map((p) => <option key={p}>{p}</option>)}</select></Field>
              <Field label="Business group"><select value={opts.bg} onChange={(e) => setOpts({ ...opts, bg: e.target.value })}>{['BG-A + BG-B', 'BG-A', 'BG-B'].map((p) => <option key={p}>{p}</option>)}</select></Field>
              <Field label="Population"><div className="unit-in"><input type="number" min="1" max="100" value={opts.sampling} onChange={(e) => setOpts({ ...opts, sampling: Number(e.target.value) })} /><span>%</span></div></Field>
            </div>

            {kri.steps && kri.steps.length > 0 && (
              <div style={{ marginTop: '14px', marginBottom: '10px' }}>
                <span className="field-label">Configured test steps to execute ({kri.steps.length})</span>
                <ol style={{ margin: '4px 0 0', paddingLeft: '18px', color: 'var(--muted)', fontSize: '12px', lineHeight: '1.4' }}>
                  {kri.steps.map((st, i) => (
                    <li key={i} style={{ marginBottom: '4px' }}>{st}</li>
                  ))}
                </ol>
              </div>
            )}

            <p className="muted small" style={{ marginTop: '10px' }}>This run executes the configured test steps and thresholds. Change those in the KRI library.</p>
            <div className="drawer-foot"><Btn onClick={onClose}>Cancel</Btn><Btn kind="primary" onClick={start}>Start run</Btn></div>
          </div>
        ) : (
          <div>
            <div className="muted small" style={{ marginBottom: '12px' }}>
              Executing {runStages.length} automated test and audit stages...
            </div>
            <ol className="progress" style={{ maxHeight: '360px', overflowY: 'auto' }}>
              {runStages.map(([label, src], i) => (
                <li key={i} className={i < stage ? 'done' : i === stage ? 'now' : ''}>
                  <span className="dot" />
                  <span style={{ flex: 1 }}>{label}</span>
                  {src && i <= stage && <span className="muted small" style={{ marginLeft: '8px' }}> · {srcName(src)}</span>}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}

