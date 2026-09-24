import React, { useState, useEffect } from 'react';
import { fmtDate, fmtMoney } from '../constants.js';
import { Btn, Tag, Field, SourceChip, classTone, classLabel } from '../ui.jsx';
export function Exceptions({ excs, kris, runs, kriById, focusExc, setFocusExc, updateExc, sendToPlanner, notify, setView, setFocusRunId }) {
  const [fClass, setFClass] = useState('all');
  const [fKri, setFKri] = useState('all');
  const [fStatus, setFStatus] = useState('open');
  const shown = excs.filter((e) => (fClass === 'all' || e.classification === fClass) && (fKri === 'all' || e.kriId === fKri) && (fStatus === 'all' || e.status === fStatus));
  const sel = excs.find((e) => e.id === focusExc);
  useEffect(() => { if (!sel && shown.length) setFocusExc(shown[0].id); }, [shown.length]);

  return (
    <section>
      <header className="page-head"><h1>Exceptions</h1><p className="lede">Each item shows what the agent pulled, why it was flagged and its best explanation. You decide; the agent does not close anything on its own.</p></header>
      <div className="filters">
        {[['open', 'Open'], ['accepted', 'Accepted'], ['anomaly', 'Confirmed anomaly'], ['planner', 'In planner'], ['all', 'All']].map(([v, l]) => <button key={v} className={fStatus === v ? 'on' : ''} onClick={() => setFStatus(v)}>{l}</button>)}
        <span className="sep" />
        {[['all', 'Any class'], ['unexplained', 'Unexplained'], ['explainable', 'Explainable']].map(([v, l]) => <button key={v} className={fClass === v ? 'on' : ''} onClick={() => setFClass(v)}>{l}</button>)}
        <span className="sep" />
        <select value={fKri} onChange={(e) => setFKri(e.target.value)}><option value="all">Any KRI</option>{kris.map((k) => <option key={k.id} value={k.id}>{k.id}</option>)}</select>
      </div>
      <div className="split">
        <ul className="exc-list">
          {shown.map((e) => (
            <li key={e.id} className={e.id === focusExc ? 'on' : ''} onClick={() => setFocusExc(e.id)}>
              <div className="exc-top"><strong>{e.ref}</strong><Tag tone={classTone(e.classification)}>{classLabel(e.classification)}</Tag></div>
              <div>{e.project}</div>
              <div className="muted small">{e.kriId} · {e.region} / {e.bg} · {fmtMoney(e.amount, e.currency)}</div>
            </li>
          ))}
          {!shown.length && <li className="empty">No exceptions match these filters.</li>}
        </ul>
        {sel ? <ExceptionDetail exc={sel} kri={kriById(sel.kriId)} run={runs.find((r) => r.id === sel.runId)} updateExc={updateExc} sendToPlanner={sendToPlanner} notify={notify} setView={setView} setFocusRunId={setFocusRunId} /> : <div className="detail empty">Select an exception.</div>}
      </div>
    </section>
  );
}

export function ExceptionDetail({ exc, kri, run, updateExc, sendToPlanner, notify, setView, setFocusRunId }) {
  const [note, setNote] = useState('');
  const pct = Math.round((exc.hypothesis?.confidence || 0.95) * 100);
  const act = (status, msg) => { updateExc(exc.id, { status, reviewNote: note || exc.reviewNote }); notify(msg); };
  const evidenceList = exc.evidence || [];
  const reasoningList = exc.reasoning || [];
  const hypText = exc.hypothesis?.text || exc.hypothesis?.summary || exc.summary || 'Variance identified against threshold.';
  const statusMap = { open: 'Open', accepted: 'Accepted', anomaly: 'Confirmed anomaly', planner: 'In planner', signed: 'Accepted' };

  return (
    <div className="detail">
      <div className="detail-head">
        <div>
          <div className="muted small">
            {exc.id} · {kri ? kri.name : (exc.kriName || exc.kriId)} · run{' '}
            {exc.runId ? (
              <span
                style={{ cursor: 'pointer', textDecoration: 'underline', color: 'var(--accent)', fontWeight: 600 }}
                onClick={() => { if (setFocusRunId) setFocusRunId(exc.runId); if (setView) setView('logs'); }}
                title="Inspect execution trace & logs for this run"
              >
                {exc.runId}
              </span>
            ) : '—'}
            {run?.period ? ` (${run.period})` : (exc.period ? ` (${exc.period})` : '')}
          </div>
          <h2>{exc.category || 'Amount Mismatch'}</h2>
        </div>
        <Tag tone={classTone(exc.classification || 'explainable')}>{classLabel(exc.classification || 'explainable')}</Tag>
      </div>
      <p className="summary">{exc.summary || ''}</p>
      <dl className="facts">
        <div><dt>Reference</dt><dd>{exc.ref || exc.id}</dd></div>
        <div><dt>WBS</dt><dd>{exc.wbs || `WBS-${exc.ref || exc.id}`}</dd></div>
        <div><dt>Project</dt><dd>{exc.project || `Customer Order ${exc.ref || exc.id}`}</dd></div>
        <div><dt>Region / BG</dt><dd>{exc.region || 'EMEA'} / {exc.bg || 'Commercial Operations'}</dd></div>
        <div><dt>Amount</dt><dd>{fmtMoney(exc.amount || 0, exc.currency || 'EUR')}</dd></div>
        <div><dt>Status</dt><dd>{statusMap[exc.status] || 'Open'}</dd></div>
      </dl>

      <h3>What the agent pulled</h3>
      <table className="tbl compact evidence">
        <tbody>
          {evidenceList.map((ev, i) => (
            <tr key={i}>
              <td><SourceChip id={ev.source || 'sap'} /></td>
              <td>{ev.record || ''}</td>
              <td className="wrap">{ev.value || ''}</td>
            </tr>
          ))}
          {!evidenceList.length && <tr><td colSpan="3" className="muted small">No direct raw records attached.</td></tr>}
        </tbody>
      </table>

      <h3>Why it was flagged</h3>
      <ol className="reason">
        {reasoningList.map((r, i) => <li key={i}>{r}</li>)}
        {!reasoningList.length && <li>Item exceeded configured audit tolerance threshold.</li>}
      </ol>

      <h3>Most likely explanation</h3>
      <div className="hyp">
        <div className="hyp-bar" aria-hidden="true"><div style={{ width: `${pct}%` }} /></div>
        <div className="hyp-txt"><strong>{pct}% confidence</strong> — {hypText}</div>
        {exc.confirm && <div className="muted small">What would confirm it: {exc.confirm}</div>}
      </div>

      {exc.status === 'open' ? (
        <div className="review">
          <Field label="Reviewer note">
            <textarea rows="2" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional. Recorded with your decision." />
          </Field>
          <div className="actions">
            <Btn onClick={() => act('accepted', `${exc.id} accepted as explainable`)}>Accept explanation</Btn>
            <Btn kind="danger" onClick={() => act('anomaly', `${exc.id} confirmed as an anomaly`)}>Confirm anomaly</Btn>
            <Btn kind="primary" onClick={() => sendToPlanner(exc)}>Send to planner as spot-audit candidate</Btn>
          </div>
        </div>
      ) : (
        <div className="review muted">Decision recorded{exc.reviewNote ? `: ${exc.reviewNote}` : ''}. <Btn kind="link" onClick={() => act('open', `${exc.id} reopened`)}>Reopen</Btn></div>
      )}
    </div>
  );
}

