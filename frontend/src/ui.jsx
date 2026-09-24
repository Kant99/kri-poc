import React from 'react';
import { srcName } from './constants.js';

export const Tag = ({ tone = 'neutral', children }) => <span className={`tag tag-${tone}`}>{children}</span>;
export const statusTone = (s) => ({ active: 'ok', paused: 'warn', draft: 'neutral', handed: 'info' }[s] || 'neutral');
export const statusLabel = (s) => ({ active: 'Active', paused: 'Paused', draft: 'In assessment', handed: 'Handed to business' }[s] || s);
export const classTone = (c) => ({ unexplained: 'bad', explainable: 'ok', review: 'warn' }[c] || 'neutral');
export const classLabel = (c) => ({ unexplained: 'Unexplained', explainable: 'Explainable', review: 'Needs review' }[c] || c);

export const Btn = ({ kind = 'default', children, ...p }) => <button className={`btn btn-${kind}`} {...p}>{children}</button>;

export const Field = ({ label, hint, children }) => (
  <label className="field"><span className="field-label">{label}</span>{children}{hint && <span className="field-hint">{hint}</span>}</label>
);

export const Drawer = ({ title, onClose, children, wide }) => (
  <div className="scrim" onClick={onClose}>
    <aside className={`drawer${wide ? ' drawer-wide' : ''}`} onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
      <header className="drawer-head"><h2>{title}</h2><button className="x" onClick={onClose} aria-label="Close">×</button></header>
      <div className="drawer-body">{children}</div>
    </aside>
  </div>
);

export const SourceChip = ({ id }) => <span className={`src src-${id}`}>{srcName(id)}</span>;

