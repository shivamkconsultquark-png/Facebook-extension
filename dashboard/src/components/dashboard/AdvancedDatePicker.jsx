// Same component as the main app's AdvancedDatePicker (finance.opcozero.com/twitter): a preset list
// (Today … Last Year) plus a Custom Range with two inline calendars and Cancel / Apply.
// Props: startDate, endDate, onChange([start, end]), label, compact, isCompare, isSingleDate, maxRangeDays,
// and maxDate (days after it can't be picked in the calendars).
import React, { useEffect, useRef, useState } from 'react';
import DatePicker from 'react-datepicker';
import { Calendar, ChevronDown } from 'lucide-react';
import {
  endOfMonth, endOfWeek, endOfYear, format, startOfDay, startOfMonth, startOfWeek, startOfYear,
  subDays, subMonths, subWeeks, subYears,
} from 'date-fns';
import 'react-datepicker/dist/react-datepicker.css';
import './AdvancedDatePicker.css';

const today = () => startOfDay(new Date());
const yesterday = () => subDays(today(), 1);
const day = (d) => format(d, 'yyyy-MM-dd');

const SINGLE_PRESETS = [
  { label: 'Today', getRange: () => [today(), today()] },
  { label: 'Yesterday', getRange: () => [yesterday(), yesterday()] },
  { label: 'Custom Date', isCustom: true },
];
const RANGE_PRESETS = [
  { label: 'Today', getRange: () => [today(), today()] },
  { label: 'Yesterday', getRange: () => [yesterday(), yesterday()] },
  { label: 'Last 7 Days', getRange: () => [subDays(today(), 6), today()] },
  { label: 'This Week', getRange: () => [startOfWeek(today(), { weekStartsOn: 1 }), today()] },
  {
    label: 'Last Week',
    getRange: () => {
      const d = subWeeks(today(), 1);
      return [startOfWeek(d, { weekStartsOn: 1 }), startOfDay(endOfWeek(d, { weekStartsOn: 1 }))];
    },
  },
  { label: 'This Month', getRange: () => [startOfMonth(today()), today()] },
  {
    label: 'Last Month',
    getRange: () => {
      const d = subMonths(today(), 1);
      return [startOfMonth(d), startOfDay(endOfMonth(d))];
    },
  },
  { label: 'This Year', getRange: () => [startOfYear(today()), today()] },
  { label: 'Last Year', getRange: () => [startOfYear(subYears(today(), 1)), startOfDay(endOfYear(subYears(today(), 1)))] },
  { label: 'Custom Range', isCustom: true },
];

export default function AdvancedDatePicker({
  startDate, endDate, onChange, label, isCompare, isSingleDate, maxRangeDays, compact, maxDate,
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const [custom, setCustom] = useState(false);
  const [draft, setDraft] = useState([startDate, endDate]);
  const [placement, setPlacement] = useState('bottom');

  const all = isSingleDate ? SINGLE_PRESETS : RANGE_PRESETS;
  const presets = maxRangeDays
    ? all.filter((p) => {
      if (p.isCustom) return true;
      const [s, e] = p.getRange();
      return Math.ceil(Math.abs(e - s) / 86400000) + 1 <= maxRangeDays;
    })
    : all;

  const labelFor = (s = startDate, e = endDate) => {
    if (isSingleDate) {
      if (!s) return 'Select Date';
      for (const p of presets) if (p.getRange && day(p.getRange()[1]) === day(s)) return p.label;
      return format(s, 'MM/dd/yyyy');
    }
    if (!s || !e) return 'Select Range';
    for (const p of presets) {
      if (!p.getRange) continue;
      const [ps, pe] = p.getRange();
      if (day(ps) === day(s) && day(pe) === day(e)) return p.label;
    }
    return `${format(s, 'MM/dd/yyyy')} - ${format(e, 'MM/dd/yyyy')}`;
  };

  const pick = (p) => {
    if (p.isCustom) { setCustom(true); return; }
    const [s, e] = p.getRange();
    onChange(isSingleDate ? e : [s, e]);
    setOpen(false);
  };
  const apply = () => {
    onChange(isSingleDate ? draft[0] : draft);
    setOpen(false);
  };

  useEffect(() => {
    if (!open || !ref.current) return;
    setDraft([startDate, endDate]);
    const r = ref.current.getBoundingClientRect();
    const below = window.innerHeight - r.bottom;
    setPlacement(below < 400 && r.top > below ? 'top' : 'bottom');
    const current = labelFor();
    const isPreset = presets.some((p) => p.label === current && !p.isCustom);
    setCustom(!isPreset && current !== 'Select Range' && current !== 'Select Date');
  }, [open, startDate, endDate]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const current = labelFor();
  const complete = isSingleDate ? !!draft[0] : !!(draft[0] && draft[1]);

  return (
    <div className="relative" ref={ref}>
      <div
        className={compact
          ? 'flex items-center gap-2 cursor-pointer group/date h-9 px-3 rounded-lg transition-all duration-300'
          : 'flex flex-col gap-0.5 cursor-pointer group/date px-3 py-1.5 rounded-xl hover:bg-[var(--hover-bg)] hover:shadow-sm transition-all duration-300 border border-transparent hover:border-[var(--border-color)]'}
        onClick={() => setOpen(!open)}
        role="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={label}
      >
        {compact
          ? <Calendar size={13} className="text-[var(--text-muted)] shrink-0" />
          : <span className="text-[9px] font-black uppercase tracking-[0.15em] text-[var(--text-muted)] group-hover/date:text-[var(--text-primary)] transition-colors">{label}</span>}
        <div className="flex items-center gap-2">
          <span className={compact
            ? 'text-xs font-semibold text-[var(--text-primary)] whitespace-nowrap'
            : `text-[13px] font-black tracking-tight ${isCompare ? 'text-[var(--text-muted)] group-hover/date:text-[var(--text-primary)]' : 'text-[var(--text-primary)]'}`}
          >
            {current}
          </span>
          <div className={`p-0.5 rounded-full transition-all duration-300 ${open ? 'bg-[var(--text-primary)] text-[var(--bg-primary)]' : 'text-[var(--text-muted)] group-hover/date:text-[var(--text-primary)]'}`}>
            <ChevronDown size={10} className={`transition-transform duration-500 ${open ? 'rotate-180' : ''}`} />
          </div>
        </div>
      </div>

      {open && (
        <div
          role="dialog"
          className={`absolute ${placement === 'bottom' ? 'top-full mt-3 origin-top-right' : 'bottom-full mb-3 origin-bottom-right'} right-0 bg-[var(--bg-primary)] rounded-[1rem] shadow-[0_20px_50px_rgba(0,0,0,0.15)] border border-[var(--border-color)] p-1.5 z-[100] adp-pop overflow-hidden flex flex-col md:flex-row transition-all duration-500 ${custom ? 'w-full min-w-[300px] md:w-[645px]' : 'w-36'}`}
        >
          <div className={`w-full ${custom ? 'md:w-32 border-r border-[var(--border-color)] pr-1' : ''} py-1 flex flex-col gap-0.5`}>
            {presets.map((p) => {
              const customActive = p.isCustom && !presets.some((q) => q.label === current && !q.isCustom);
              const active = current === p.label;
              return (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => pick(p)}
                  className={`w-full text-left px-4 py-2 rounded-md text-[12px] font-bold transition-all duration-300 relative group/p ${customActive
                    ? 'bg-[var(--text-primary)] text-[var(--bg-primary)]'
                    : active
                      ? 'bg-[var(--bg-secondary)] text-[var(--text-primary)]'
                      : 'hover:bg-[var(--hover-bg)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
                >
                  <span className="relative z-10">{p.label}</span>
                  {active && !p.isCustom && <div className="absolute right-3 top-1/2 -translate-y-1/2 w-1 h-1 bg-[var(--text-primary)] rounded-full" />}
                </button>
              );
            })}
          </div>

          {custom && (
            <div className="flex-1 px-2 py-1 flex flex-col justify-between">
              <div className="w-full h-full flex items-center justify-center premium-datepicker-container">
                <DatePicker
                  selected={isSingleDate ? draft[0] : null}
                  onChange={(v) => setDraft(isSingleDate ? [v, v] : v)}
                  startDate={isSingleDate ? null : draft[0]}
                  endDate={isSingleDate ? null : draft[1]}
                  selectsRange={!isSingleDate}
                  inline
                  monthsShown={isSingleDate ? 1 : 2}
                  maxDate={maxDate}
                  calendarClassName="premium-calendar"
                />
              </div>
              <div className="mt-4 w-full flex items-center justify-between border-t border-[var(--border-color)] pt-4 px-2">
                <div className="flex flex-col">
                  <span className="text-[9px] font-black uppercase tracking-widest text-[var(--text-muted)]">
                    {isSingleDate ? 'Selected Date' : 'Selected Range'}
                  </span>
                  <span className="text-[11px] font-bold text-[var(--text-primary)]">{labelFor(draft[0], draft[1])}</span>
                </div>
                <div className="flex items-center gap-4">
                  <button type="button" onClick={() => setOpen(false)}
                    className="px-4 py-2 text-[11px] font-bold text-[var(--text-primary)] border-none hover:opacity-70 transition-opacity">
                    Cancel
                  </button>
                  <button type="button" onClick={apply} disabled={!complete}
                    className="px-8 py-2 bg-[var(--text-primary)] text-[var(--bg-primary)] rounded-md text-[11px] font-bold hover:opacity-80 transition-all duration-300 shadow-lg shadow-black/10 disabled:opacity-40 disabled:cursor-not-allowed">
                    Apply
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
