import React, { useState, useEffect } from 'react';
import { fmtDate } from '../constants.js';
import { Btn, Tag, Field, Drawer } from '../ui.jsx';
export function Planner({ planner, reference, addPlannerItem, updatePlanner, draftPlan, notify }) {
  const [sel, setSel] = useState(planner[0] ? planner[0].id : null);
  const [showNew, setShowNew] = useState(false);
  const item = planner.find((p) => p.id === sel);
  const [draft, setDraft] = useState({ title: '', region: 'MEA', bg: 'BG-A', scope: '', suggestedLength: '1 week' });

  return (
    <section>
      <header className="page-head row">
        <div><h1>Audit planner</h1><p className="lede">Turns a trigger into pointed audit steps built on the latest policy and the last three work papers. Long process audits stay in the traditional workflow; this is for 1–3 week spot audits.</p></div>
        <div className="actions"><Btn kind="primary" onClick={() => setShowNew(true)}>Add a manual entry</Btn></div>
      </header>
      <div className="split">
        <ul className="exc-list">
          {planner.map((p) => (
            <li key={p.id} className={p.id === sel ? 'on' : ''} onClick={() => setSel(p.id)}>
              <div className="exc-top"><strong>{p.id}</strong><Tag tone={p.status === 'New' ? 'warn' : p.status === 'Live' ? 'ok' : 'info'}>{p.status}</Tag></div>
              <div>{p.title}</div>
              <div className="muted small">{p.type} · {p.source} · {p.region} / {p.bg}</div>
            </li>
          ))}
        </ul>
        {item ? <PlanView item={item} reference={reference} updatePlanner={updatePlanner} draftPlan={draftPlan} /> : <div className="detail empty">Select a trigger.</div>}
      </div>
      {showNew && (
        <Drawer title="Add a manual entry" onClose={() => setShowNew(false)}>
          <p className="muted">For a known spot audit from the annual plan or a request that did not come through the ticketing system.</p>
          <Field label="Title"><input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
          <div className="grid2">
            <Field label="Region"><select value={draft.region} onChange={(e) => setDraft({ ...draft, region: e.target.value })}>{['MEA', 'Europe', 'APAC', 'Americas'].map((r) => <option key={r}>{r}</option>)}</select></Field>
            <Field label="Business group"><select value={draft.bg} onChange={(e) => setDraft({ ...draft, bg: e.target.value })}>{['BG-A', 'BG-B'].map((r) => <option key={r}>{r}</option>)}</select></Field>
          </div>
          <Field label="Scope"><textarea rows="3" value={draft.scope} onChange={(e) => setDraft({ ...draft, scope: e.target.value })} /></Field>
          <Field label="Suggested length"><select value={draft.suggestedLength} onChange={(e) => setDraft({ ...draft, suggestedLength: e.target.value })}>{['1 week', '2 weeks', '3 weeks'].map((r) => <option key={r}>{r}</option>)}</select></Field>
          <div className="drawer-foot"><Btn onClick={() => setShowNew(false)}>Cancel</Btn><Btn kind="primary" disabled={!draft.title.trim()} onClick={() => addPlannerItem(draft).then((item) => {
            if (!item) return;
            setSel(item.id); setShowNew(false); setDraft({ title: '', region: 'MEA', bg: 'BG-A', scope: '', suggestedLength: '1 week' });
          })}>Add</Btn></div>
        </Drawer>
      )}
    </section>
  );
}

export function PlanView({ item, reference, updatePlanner, draftPlan }) {
  const [policy, setPolicy] = useState(item.policyDefault || 'v4.1');
  const [plan, setPlan] = useState(item.plan || null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setPlan(item.plan || null); setPolicy(item.policyDefault || 'v4.1'); }, [item.id]);
  const POLICIES = reference.policies, PRIOR_WORK = reference.priorWork;
  const prior = PRIOR_WORK.filter((w) => w.policy !== policy);
  const warn = PRIOR_WORK.length > 0 && prior.length === PRIOR_WORK.length;

  const generate = () => {
    setBusy(true);
    draftPlan(item.id, policy).then((saved) => { if (saved) setPlan(saved.plan); }).finally(() => setBusy(false));
  };
  const toneOf = { kept: 'neutral', updated: 'info', new: 'ok', dropped: 'bad' };
  const counts = plan ? plan.reduce((a, s) => ({ ...a, [s.status]: (a[s.status] || 0) + 1 }), {}) : {};

  return (
    <div className="detail">
      <div className="detail-head"><div><div className="muted small">{item.id} · {item.type} · {item.source} · created {fmtDate(item.createdAt)}</div><h2>{item.title}</h2></div><Tag tone={item.status === 'New' ? 'warn' : item.status === 'Live' ? 'ok' : 'info'}>{item.status}</Tag></div>
      <p className="summary">{item.scope}</p>
      <dl className="facts">
        <div><dt>Region / BG</dt><dd>{item.region} / {item.bg}</dd></div><div><dt>Suggested length</dt><dd>{item.suggestedLength}</dd></div><div><dt>Linked exceptions</dt><dd>{item.link}</dd></div>
      </dl>

      <h3>Benchmark policy</h3>
      <div className="grid2">
        <Field label="Policy the plan is built against"><select value={policy} onChange={(e) => setPolicy(e.target.value)} disabled={item.status === 'Live'}>{POLICIES.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.date}{p.current ? ' · current' : ''}</option>)}</select></Field>
        <div className="field"><span className="field-label">Prior work papers found</span>
          <ul className="prior">{PRIOR_WORK.map((w) => <li key={w.audit}>{w.audit} · cited {w.policy} · {w.steps} steps · {w.date}</li>)}</ul></div>
      </div>
      {warn && <div className="callout">You are citing policy {policy}, but the last {PRIOR_WORK.length} Order Intake audits used {[...new Set(PRIOR_WORK.map((w) => w.policy))].join(' / ')}. Are you sure? The plan will still be written against the current rules and note the difference.</div>}

      {!plan ? (
        <div className="review"><Btn kind="primary" disabled={busy} onClick={generate}>{busy ? 'Reading policy and prior work…' : 'Draft the audit steps'}</Btn></div>
      ) : (
        <div>
          <h3>Audit steps <span className="muted small">— {counts.new || 0} new · {counts.updated || 0} updated · {counts.kept || 0} kept · {counts.dropped || 0} dropped from the legacy checklist</span></h3>
          <ol className="plan">
            {plan.map((s, i) => (
              <li key={i} className={s.status === 'dropped' ? 'dropped' : ''}><div className="plan-top"><Tag tone={toneOf[s.status]}>{s.status}</Tag><span>{s.step}</span></div><div className="muted small">{s.note}</div></li>
            ))}
          </ol>
          {item.status !== 'Live' ? (
            <div className="review"><div className="actions">
              <Btn onClick={() => updatePlanner(item.id, { clearPlan: true, status: 'New' }).then(() => setPlan(null))}>Discard draft</Btn>
              <Btn kind="primary" onClick={() => updatePlanner(item.id, { status: 'Live' }, `${item.id} is live – the ${item.suggestedLength} spot audit starts`)}>Manager approves – make it live</Btn>
            </div><p className="muted small">The plan itself has no human-in-the-loop; the manager's approval is the gate before execution.</p></div>
          ) : <div className="review muted">Live. Execution and the audit ticket continue in the traditional workflow.</div>}
        </div>
      )}
    </div>
  );
}

