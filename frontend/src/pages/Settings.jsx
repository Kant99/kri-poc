import React, { useState, useEffect } from 'react';
import { Btn, Tag, Field, SourceChip } from '../ui.jsx';
export function Settings({ settings, saveCalendar, resetData }) {
  const [cal, setCal] = useState(settings ? settings.calendar : { pattern: '4-4-5', fyStart: 'January', weekEnd: 'Sunday' });
  const [reset, setReset] = useState(false);
  return (
    <section>
      <header className="page-head"><h1>Settings</h1><p className="lede">Connections use shared agent accounts, not personal IDs, so no run depends on a single person.</p></header>
      <div className="cols">
        <div className="col">
          <h3>Financial calendar</h3>
          <div className="grid2">
            <Field label="Close pattern"><select value={cal.pattern} onChange={(e) => setCal({ ...cal, pattern: e.target.value })}>{['4-4-5', '4-5-4', '5-4-4', 'Calendar month'].map((p) => <option key={p}>{p}</option>)}</select></Field>
            <Field label="Fiscal year starts"><select value={cal.fyStart} onChange={(e) => setCal({ ...cal, fyStart: e.target.value })}>{['January', 'April', 'October'].map((p) => <option key={p}>{p}</option>)}</select></Field>
            <Field label="Week ends on"><select value={cal.weekEnd} onChange={(e) => setCal({ ...cal, weekEnd: e.target.value })}>{['Sunday', 'Saturday', 'Friday'].map((p) => <option key={p}>{p}</option>)}</select></Field>
          </div>
          <Btn onClick={() => saveCalendar(cal)}>Save calendar</Btn>
          <h3>Residual-risk routing</h3>
          <p className="muted">When a KRI's unexplained items in one region cross its planner threshold, the pattern is queued in the Audit planner for a human decision. KRIs whose triggers fall to zero for three periods are suggested for hand-over to the business.</p>
        </div>
        <div className="col">
          <h3>Connections and agent accounts</h3>
          <table className="tbl compact">
            <thead><tr><th>Source</th><th>Account</th><th>Environment</th><th>State</th></tr></thead>
            <tbody>
              {(settings ? settings.connections : []).map((c) => (
                <tr key={c.source}><td><SourceChip id={c.source} /></td><td className="mono">{c.account}</td><td>{c.environment}</td><td><Tag tone={c.state}>{c.state === 'ok' ? 'Connected' : 'Access pending'}</Tag></td></tr>
              ))}
            </tbody>
          </table>
          <h3>Demo data</h3>
          <p className="muted">Configuration and decisions are stored by the API; resetting restores the sample data for everyone.</p>
          {!reset ? <Btn kind="danger" onClick={() => setReset(true)}>Reset to sample data</Btn> : <div className="actions"><Btn onClick={() => setReset(false)}>Keep my changes</Btn><Btn kind="danger" onClick={() => resetData().then(() => setReset(false))}>Yes, reset</Btn></div>}
        </div>
      </div>
    </section>
  );
}
