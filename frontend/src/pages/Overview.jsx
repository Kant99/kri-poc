import React, { useState, useEffect } from 'react';
import { fmtDate, fmtMoney, FREQ, nextRunFor, periodLabelFor } from '../constants.js';
import { Btn, Tag } from '../ui.jsx';
export function Overview({ kris, runs, excs, planner, setView, setRunReq, kriById, setFocusExc }) {
  const active = kris.filter((k) => k.status === 'active');
  const open = excs.filter((e) => e.status === 'open');
  const unexplained = open.filter((e) => e.classification === 'unexplained');
  const tested = runs.reduce((a, r) => a + r.tested, 0);
  const upcoming = active.map((k) => ({ k, d: nextRunFor(k) })).filter((x) => x.d).sort((a, b) => a.d - b.d);
  const byRegion = {};
  unexplained.forEach((e) => { byRegion[e.region] = (byRegion[e.region] || 0) + 1; });

  return (
    <section>
      <header className="page-head">
        <h1>Where the risk is this period</h1>
        <p className="lede">Every active KRI tests the full population at its own cadence. What it cannot explain lands here, then in the planner if the pattern persists.</p>
      </header>

      <div className="stats">
        <div className="stat"><div className="stat-n">{unexplained.length}</div><div className="stat-l">unexplained exceptions open</div></div>
        <div className="stat"><div className="stat-n">{open.length - unexplained.length}</div><div className="stat-l">explainable, awaiting sign-off</div></div>
        <div className="stat"><div className="stat-n">{tested.toLocaleString()}</div><div className="stat-l">items tested at 100% population</div></div>
        <div className="stat"><div className="stat-n">{active.length}<span className="stat-of">/{kris.length}</span></div><div className="stat-l">KRIs active</div></div>
      </div>

      <div className="cols">
        <div className="col">
          <h3>Unexplained items, by region</h3>
          <div className="bars">
            {Object.entries(byRegion).sort((a, b) => b[1] - a[1]).map(([r, n]) => (
              <div className="bar-row" key={r}><span className="bar-l">{r}</span><div className="bar"><div className="bar-f" style={{ width: `${(n / Math.max(...Object.values(byRegion))) * 100}%` }} /></div><span className="bar-n">{n}</span></div>
            ))}
            {!Object.keys(byRegion).length && <p className="muted">Nothing unexplained is open.</p>}
          </div>
          <h3>Needs a decision</h3>
          <ul className="list">
            {unexplained.slice(0, 4).map((e) => (
              <li key={e.id} className="list-row" onClick={() => { setFocusExc(e.id); setView('exceptions'); }}>
                <div><strong>{e.ref}</strong> · {e.project}<div className="muted small">{kriById(e.kriId) ? kriById(e.kriId).name : e.kriId} · {e.category}</div></div>
                <div className="right"><div>{fmtMoney(e.amount, e.currency)}</div><div className="muted small">{Math.round(e.hypothesis.confidence * 100)}% confidence</div></div>
              </li>
            ))}
          </ul>
        </div>
        <div className="col">
          <h3>Next runs</h3>
          <ul className="list">
            {upcoming.map(({ k, d }) => (
              <li key={k.id} className="list-row">
                <div><strong>{k.id}</strong> · {k.name}<div className="muted small">{FREQ[k.frequency]}{k.alignToClose ? `, +${k.fetchOffsetDays}d after close` : ''} · {periodLabelFor(k)}</div></div>
                <div className="right"><div>{fmtDate(d)}</div><Btn kind="link" onClick={() => setRunReq({ kriId: k.id })}>Run now</Btn></div>
              </li>
            ))}
          </ul>
          <h3>Planner queue</h3>
          <ul className="list">
            {planner.filter((p) => p.status !== 'Live').map((p) => (
              <li key={p.id} className="list-row" onClick={() => setView('planner')}>
                <div><strong>{p.id}</strong> · {p.title}<div className="muted small">{p.type} · {p.source}</div></div>
                <Tag tone={p.status === 'New' ? 'warn' : 'info'}>{p.status}</Tag>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

