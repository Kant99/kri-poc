// Reference data and pure helpers shared by all pages.
export const TODAY = new Date('2026-09-16T09:00:00');
export const fmtDate = (d) => new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
export const fmtMoney = (n, c = 'EUR') => `${c} ${Number(n).toLocaleString('en-GB')}`;
export const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

export const SOURCES = [
  { id: 'sap', name: 'SAP ECC', kind: 'ERP (OI / YCA report, WBS, revenue)' },
  { id: 'sap_ecc', name: 'SAP ECC', kind: 'ERP (OI / YCA report, WBS, revenue)' },
  { id: 'obs', name: 'Order Booking System', kind: 'Order booking & CPO capture' },
  { id: 'red_box_po', name: 'Red Box / PO', kind: 'Centralized PO Engine' },
  { id: 'sapiens', name: 'Sapiens', kind: 'Policy Core Administration' },
  { id: 'blue_planet', name: 'Blue Planet', kind: 'Billing & Revenue Platform' },
  { id: 'ems_sharepoint', name: 'EMS / SharePoint', kind: 'Contract repository & storage' },
  { id: 'mrep', name: 'Management Reporting', kind: 'Management reporting & backlog views' },
  { id: 'pmaster', name: 'Project Master', kind: 'Project & WBS master' },
  { id: 'tickets', name: 'Audit Ticketing / SharePoint', kind: 'Audit tickets, policies, work papers' },
];
export const srcName = (id) => (SOURCES.find((s) => s.id === id) || {}).name || id;

export const FREQ = { monthly: 'Every monthly close', quarterly: 'Every quarterly close', weekly: 'Every Monday', custom: 'On a chosen date' };

export const CLOSE_CALENDAR = {
  pattern: '4-4-5',
  periods: [
    { p: 'P8', label: 'Aug 2026', close: '2026-08-30', q: 'Q3' },
    { p: 'P9', label: 'Sep 2026', close: '2026-09-27', q: 'Q3', quarterEnd: true },
    { p: 'P10', label: 'Oct 2026', close: '2026-10-25', q: 'Q4' },
    { p: 'P11', label: 'Nov 2026', close: '2026-11-22', q: 'Q4' },
    { p: 'P12', label: 'Dec 2026', close: '2026-12-27', q: 'Q4', quarterEnd: true },
  ],
};

export const nextRunFor = (k, calendar = CLOSE_CALENDAR) => {
  if (k.status !== 'active') return null;
  if (k.frequency === 'weekly') { const d = new Date(TODAY); d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7)); return d; }
  if (k.frequency === 'custom') return k.customDate ? new Date(k.customDate) : null;
  const periods = calendar.periods.filter((p) => new Date(p.close) >= TODAY && (k.frequency === 'monthly' || p.quarterEnd));
  if (!periods.length) return null;
  return addDays(periods[0].close, k.fetchOffsetDays || 0);
};
export const periodLabelFor = (k) => {
  if (k.frequency === 'weekly') return 'Week to date';
  const p = CLOSE_CALENDAR.periods.find((x) => new Date(x.close) >= TODAY && (k.frequency === 'monthly' || x.quarterEnd));
  return p ? (k.frequency === 'monthly' ? `${p.p} · ${p.label}` : `${p.q} FY26`) : '—';
};



export const STAGES = [
  ['Connecting with the agent service account', 'sap'],
  ['Extracting the full population for the period', 'obs'],
  ['Running the configured test steps', null],
  ['Cross-checking against the other sources', 'mrep'],
  ['Writing the reasoning for each exception', null],
  ['Populating the testing template and report', 'tickets'],
];
