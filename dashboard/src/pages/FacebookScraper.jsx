import React, { useState, useEffect, useCallback } from 'react';
import { toZonedTime } from '../utils/timezone';
import { useAuth } from '../context/AuthContext';
import { X, AlertCircle, Loader2 } from 'lucide-react';
import { API_MAIN } from '../config/api';

// The Facebook service can run inside the main API (same routers) or standalone.
const API_FB = (import.meta.env && import.meta.env.VITE_FB_SHIELD_API) || API_MAIN;

/**
 * FacebookScraper: one-click, fully automated (Facebook counterpart of AutoScraper).
 * A single button starts a backend-controlled Chrome window that scrapes
 * the Facebook home feed every 5 minutes. No extension needed —
 * the user only links Facebook once (session import or a login window).
 */
const INTERVAL_OPTIONS = [
  { label: '2 minutes', value: 120 },
  { label: '3 minutes', value: 180 },
  { label: '5 minutes', value: 300 },
  { label: '10 minutes', value: 600 },
  { label: '15 minutes', value: 900 },
  { label: '30 minutes', value: 1800 },
  { label: '1 hour', value: 3600 },
  { label: '2 hours', value: 7200 },
  { label: '4 hours', value: 14400 },
  { label: '8 hours', value: 28800 },
  { label: '12 hours', value: 43200 },
  { label: '24 hours (1 day)', value: 86400 },
];

export default function FacebookScraper({ embedded = false, onClose }) {
  const { token, user } = useAuth();
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [showImport, setShowImport] = useState(false);
  const [cookiesText, setCookiesText] = useState('');
  const [importMsg, setImportMsg] = useState(null);
  const [intervalSeconds, setIntervalSeconds] = useState(300);
  const [showInstructions, setShowInstructions] = useState(false);
  const [instructionsText, setInstructionsText] = useState('');
  const [instructionsMsg, setInstructionsMsg] = useState(null);

  const authHeaders = useCallback(() => {
    const headers = {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      'X-Requested-With': 'XMLHttpRequest',
    };
    if (user?.company_id) headers['X-Company-Id'] = user.company_id;
    if (user?.table_prefix) headers['X-Table-Prefix'] = user.table_prefix;
    return headers;
  }, [token, user]);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_FB}/facebook/browser-scraper/status`, { headers: authHeaders() });
      if (res.ok) {
        setStatus(await res.json());
        setError(null);
      }
    } catch (e) {
      setError(e.message);
    }
  }, [authHeaders]);

  // Poll status every 5 seconds
  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, 5000);
    return () => clearInterval(timer);
  }, [fetchStatus]);

  // Open the session-import block automatically while no Facebook account is linked
  useEffect(() => {
    if (status && !status.fb_account) setShowImport(true);
  }, [status?.fb_account, status]);

  // Sync the dropdown to the live interval only while the scraper is running.
  // When stopped, the status poll reports the default/last interval every 5s
  // and would silently reset the user's selection before they click Start.
  useEffect(() => {
    if (status?.running && status?.interval_seconds) {
      setIntervalSeconds(status.interval_seconds);
    }
  }, [status]);

  const callAction = async (action, bodyData = null) => {
    setBusy(true);
    setError(null);
    try {
      const options = {
        method: 'POST',
        headers: authHeaders(),
      };
      if (bodyData) {
        options.body = JSON.stringify(bodyData);
      }
      const res = await fetch(`${API_FB}/facebook/browser-scraper/${action}`, options);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const fetchInstructions = async () => {
    try {
      const res = await fetch(`${API_FB}/facebook/settings/llm-instructions`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        setInstructionsText(data.instructions);
      }
    } catch (e) {
      setInstructionsMsg({ type: 'error', text: `Failed to load instructions: ${e.message}` });
    }
  };

  const handleSaveInstructions = async () => {
    setBusy(true);
    setInstructionsMsg(null);
    try {
      const res = await fetch(`${API_FB}/facebook/settings/llm-instructions`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ instructions: instructionsText }),
      });
      if (res.ok) {
        const data = await res.json();
        setInstructionsText(data.instructions);
        setInstructionsMsg({ type: 'success', text: 'Instructions updated successfully!' });
      } else {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
    } catch (e) {
      setInstructionsMsg({ type: 'error', text: `Failed to save: ${e.message}` });
    } finally {
      setBusy(false);
    }
  };

  const handleRestoreDefault = async () => {
    if (!window.confirm('Restore instructions to system default?')) return;
    setBusy(true);
    setInstructionsMsg(null);
    try {
      const res = await fetch(`${API_FB}/facebook/settings/llm-instructions`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ restore_default: true }),
      });
      if (res.ok) {
        const data = await res.json();
        setInstructionsText(data.instructions);
        setInstructionsMsg({ type: 'success', text: 'Restored default instructions successfully!' });
      } else {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
    } catch (e) {
      setInstructionsMsg({ type: 'error', text: `Failed to restore: ${e.message}` });
    } finally {
      setBusy(false);
    }
  };

  // Live client-side validation of the pasted cookie export.
  // Facebook's login cookies are c_user (account id) and xs (session).
  const cookieCheck = (() => {
    const text = cookiesText.trim();
    if (!text) return null;
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      // Allow a raw "name=value; ..." string too
      if (text.includes('c_user=') && text.includes('xs=')) return { ok: true, count: text.split(';').length, note: 'Cookie string detected.' };
      return { ok: false, note: 'This is not valid JSON. Use Cookie-Editor → Export as JSON, then paste.' };
    }
    if (!Array.isArray(data)) {
      if (data && data.c_user && data.xs) return { ok: true, count: Object.keys(data).length, note: 'Cookie object detected.' };
      return { ok: false, note: 'Expected the Cookie-Editor JSON list. Re-export with "Export as JSON".' };
    }
    const has = (name) => data.some((c) => c && c.name === name && c.value);
    if (!has('c_user') || !has('xs')) return { ok: false, note: `Found ${data.length} cookies, but not the "c_user" and "xs" login cookies. Make sure you're logged into facebook.com, then re-export.` };
    return { ok: true, count: data.length, note: `Valid — ${data.length} cookies including the login token.` };
  })();

  const importSession = async () => {
    setBusy(true);
    setError(null);
    setImportMsg(null);
    try {
      const res = await fetch(`${API_FB}/facebook/browser-scraper/import-session`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ cookies: cookiesText }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      setImportMsg({ ok: body.ok, text: body.detail });
      if (body.ok) setCookiesText('');
      await fetchStatus();
    } catch (e) {
      setImportMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const running = status?.running;
  const needsLogin = status?.needs_login;
  const accountLinked = Boolean(status?.fb_account);
  const loginWindowAvailable = status?.login_window_available !== false;

  const nextRunLabel = () => {
    if (!status?.next_run_at) return null;
    const diff = Math.max(0, Math.round((new Date(status.next_run_at) - new Date()) / 1000));
    return `${Math.floor(diff / 60)}:${String(diff % 60).padStart(2, '0')}`;
  };

  const getStatusColor = () => {
    if (status?.error) return 'bg-red-50 border-red-200';
    if (needsLogin) return 'bg-amber-50 border-amber-200';
    if (running) return 'bg-green-50 border-green-200';
    return 'bg-white border-zinc-200';
  };

  const loginWindowButton = loginWindowAvailable && (
    <button
      onClick={() => callAction('login-window')}
      disabled={busy || status?.login_window_open}
      className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-white text-blue-700 border border-blue-200 rounded-lg font-bold text-sm hover:bg-blue-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {status?.login_window_open ? <Loader2 size={16} className="animate-spin" /> : '🌐'}
      {status?.login_window_open ? 'Waiting for you to log in…' : 'Log in to Facebook (opens a Chrome window)'}
    </button>
  );

  return (
    <div className={embedded
      ? 'w-full'
      : 'flex flex-col items-center justify-center min-h-screen bg-gradient-to-br from-blue-50 to-zinc-100 p-8'}>
      <div className={`w-full max-w-lg p-8 rounded-2xl border-2 ${getStatusColor()} shadow-lg ${embedded ? 'mx-auto' : ''}`}>
        <h1 className="text-3xl font-black text-zinc-900 mb-2 text-center">
          Facebook <span className="text-blue-500">Auto-Scraper</span>
        </h1>

        <p className="text-sm text-zinc-500 text-center mb-8">
          One click • Fully automatic
        </p>

        <div className="bg-white p-6 rounded-xl border border-zinc-200 mb-6">
          <p className="text-sm font-bold text-zinc-700 mb-3">Status</p>
          <p className="text-sm font-bold text-zinc-900 mb-2 leading-relaxed flex items-center gap-2">
            {running && !needsLogin && <Loader2 size={16} className="animate-spin text-blue-500 flex-shrink-0" />}
            {status?.message || 'Loading status...'}
          </p>

          {error && (
            <p className="text-xs text-red-600 font-bold mt-2">❌ {error}</p>
          )}

          {running && status?.next_run_at && !needsLogin && (
            <p className="text-xs text-zinc-500 mt-2">
              Next scrape in: <span className="font-bold text-zinc-700">{nextRunLabel()}</span>
            </p>
          )}

          {status?.cycles > 0 && (
            <div className="mt-4 pt-4 border-t border-zinc-200">
              <p className="text-sm text-green-700 font-bold">
                ✅ {status.last_count} posts in last cycle
              </p>
              <p className="text-xs text-zinc-500 mt-1">
                Total: <span className="font-bold">{status.total_scraped}</span> · Cycles: <span className="font-bold">{status.cycles}</span>
                {status.last_run && <> · Last run: <span className="font-bold">{toZonedTime(new Date(status.last_run))}</span></>}
              </p>
            </div>
          )}

          <div className="mt-4 pt-4 border-t border-zinc-200 flex items-center justify-between gap-2">
            <p className="text-xs text-zinc-500">
              Linked Facebook account:{' '}
              {status?.fb_account
                ? (status.fb_account_url
                  ? <a href={status.fb_account_url} target="_blank" rel="noreferrer" className="font-bold text-blue-600">{status.fb_account}</a>
                  : <span className="font-bold text-blue-600">{status.fb_account}</span>)
                : <span className="font-bold text-zinc-400">none yet — detected on first scrape</span>}
            </p>
            <button
              onClick={async () => {
                if (!window.confirm('Unlink the current Facebook account? Scraping stops and the saved login is removed. On the next Start, a login window opens for the new account.')) return;
                await callAction('switch-account');
              }}
              disabled={busy}
              className="text-xs font-bold text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 hover:bg-amber-100 transition-all disabled:opacity-50 whitespace-nowrap"
            >
              Switch Facebook Account
            </button>
          </div>

          <div className="mt-4 pt-4 border-t border-zinc-200 flex items-center justify-between gap-4">
            <div>
              <label className="block text-xs font-bold text-zinc-700 mb-0.5">Scrape Interval</label>
              <p className="text-[10px] text-zinc-400">
                {running ? 'Stop scraper to change' : 'Select run frequency'}
              </p>
            </div>
            <select
              value={intervalSeconds}
              onChange={(e) => setIntervalSeconds(Number(e.target.value))}
              disabled={running || busy}
              className="text-xs font-bold text-zinc-700 bg-white border border-zinc-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:opacity-60 disabled:bg-zinc-50 cursor-pointer"
            >
              {INTERVAL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="space-y-3 mb-6">
          {!running ? (
            <>
              <button
                onClick={() => callAction('start', { interval_seconds: intervalSeconds })}
                disabled={busy || !accountLinked}
                title={!accountLinked ? 'Link your Facebook account first — import your session below' : undefined}
                className="w-full flex items-center justify-center gap-2 px-6 py-3 bg-green-500 text-white rounded-xl font-bold text-sm hover:bg-green-600 transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-green-500"
              >
                {busy ? <Loader2 size={18} className="animate-spin" /> : '▶️'} Start Auto-Scraper
              </button>
              {!accountLinked && (
                <p className="text-xs text-amber-700 font-bold text-center">
                  ⚠️ No Facebook account linked — import your Facebook session below to enable this button
                </p>
              )}
            </>
          ) : (
            <button
              onClick={() => callAction('stop')}
              disabled={busy}
              className="w-full flex items-center justify-center gap-2 px-6 py-3 bg-red-500 text-white rounded-xl font-bold text-sm hover:bg-red-600 transition-all disabled:opacity-50"
            >
              {busy ? <Loader2 size={18} className="animate-spin" /> : '⏹'} Stop Auto-Scraper
            </button>
          )}
        </div>

        {needsLogin && (
          <div className="bg-amber-50 border border-amber-300 p-4 rounded-xl mb-6 space-y-3">
            <p className="text-xs text-amber-800 font-bold flex gap-2">
              <AlertCircle size={16} className="flex-shrink-0 mt-0.5" />
              Login required — import your Facebook session below to link the account. Scraping resumes automatically once the session is imported.
            </p>
            {loginWindowButton}
          </div>
        )}

        <div className="bg-blue-50 border border-blue-200 p-4 rounded-xl mb-4">
          <p className="text-xs text-blue-800 font-bold mb-2 flex gap-2">
            <AlertCircle size={16} className="flex-shrink-0 mt-0.5" />
            How it works
          </p>
          <ol className="text-xs text-blue-700 space-y-1 ml-6 list-decimal">
            <li>Import your Facebook session below (recommended), or log in once in the Chrome window</li>
            <li>Your feed is scraped automatically every {INTERVAL_OPTIONS.find(o => o.value === intervalSeconds)?.label || '5 minutes'}</li>
            <li>Posts sync automatically to your dashboard</li>
          </ol>
        </div>

        {/* Spam Score LLM Instructions */}
        <div className="bg-white border border-zinc-200 rounded-xl mb-4 overflow-hidden">
          <button
            onClick={() => {
              setShowInstructions(!showInstructions);
              if (!showInstructions) {
                fetchInstructions();
              }
            }}
            className="w-full flex items-center justify-between px-4 py-3 text-left"
          >
            <span className="text-sm font-bold text-zinc-800">🤖 Spam Score LLM Instructions</span>
            <span className="text-zinc-400 text-lg">{showInstructions ? '–' : '+'}</span>
          </button>
          {showInstructions && (
            <div className="px-4 pb-4 border-t border-zinc-100 pt-3 space-y-3">
              <p className="text-xs text-zinc-500 leading-relaxed">
                Customize the system instructions used by the LLM (Gemini) to calculate scam scores (0-100) for incoming Facebook posts.
              </p>
              <textarea
                value={instructionsText}
                onChange={(e) => setInstructionsText(e.target.value)}
                disabled={busy}
                rows={10}
                className="w-full text-xs p-3 border border-zinc-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 font-mono resize-y leading-relaxed bg-zinc-50 disabled:opacity-60"
              />
              {instructionsMsg && (
                <p className={`text-[11px] font-bold ${instructionsMsg.type === 'error' ? 'text-red-600' : 'text-green-600'}`}>
                  {instructionsMsg.text}
                </p>
              )}
              <div className="flex gap-2 justify-end pt-1">
                <button
                  onClick={handleRestoreDefault}
                  disabled={busy}
                  className="px-3 py-1.5 text-xs font-bold text-zinc-600 hover:text-zinc-800 bg-zinc-100 hover:bg-zinc-200 rounded-lg transition-all disabled:opacity-50"
                >
                  Restore Default
                </button>
                <button
                  onClick={handleSaveInstructions}
                  disabled={busy}
                  className="px-4 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-sm transition-all disabled:opacity-50"
                >
                  Save Instructions
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Session import — the reliable way to link Facebook on a server */}
        <div className={`rounded-xl mb-6 overflow-hidden border ${
          accountLinked
            ? 'bg-white border-zinc-200'
            : 'bg-amber-50 border-amber-300 ring-2 ring-amber-300 shadow-md'
        }`}>
          <button
            onClick={() => setShowImport((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-3 text-left"
          >
            <span className="flex items-center gap-2">
              <span className={`text-sm font-bold ${accountLinked ? 'text-zinc-800' : 'text-amber-900'}`}>🔑 Link Facebook account by session import</span>
              {!accountLinked && (
                <span className="text-[10px] font-bold uppercase tracking-wide text-white bg-amber-600 rounded-full px-2 py-0.5">Required</span>
              )}
            </span>
            <span className={`text-lg ${accountLinked ? 'text-zinc-400' : 'text-amber-700'}`}>{showImport ? '–' : '+'}</span>
          </button>
          {showImport && (
            <div className={`px-4 pb-4 border-t pt-3 ${accountLinked ? 'border-zinc-100' : 'border-amber-200 bg-white/60'}`}>
              <p className="text-xs text-zinc-600 mb-2 font-bold">Most reliable — no login prompts, works on servers:</p>
              <ol className="text-xs text-zinc-600 space-y-1.5 ml-4 list-decimal mb-3">
                <li>
                  Install the free <span className="font-bold">Cookie-Editor</span> extension:
                  <span className="flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5">
                    <a href="https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm" target="_blank" rel="noreferrer" className="text-blue-600 font-bold underline">Chrome / Edge</a>
                    <a href="https://addons.mozilla.org/firefox/addon/cookie-editor/" target="_blank" rel="noreferrer" className="text-blue-600 font-bold underline">Firefox</a>
                  </span>
                </li>
                <li>Open <span className="font-bold">facebook.com</span> (logged in), click the Cookie-Editor icon</li>
                <li>Click <span className="font-bold">Export</span> → <span className="font-bold">Export as JSON</span> (copies to clipboard)</li>
                <li>Paste below and click <span className="font-bold">Import Session</span></li>
              </ol>
              <textarea
                value={cookiesText}
                onChange={(e) => setCookiesText(e.target.value)}
                placeholder='Paste the Cookie-Editor JSON export here…'
                rows={4}
                className={`w-full text-xs font-mono border rounded-lg p-2 mb-1 focus:outline-none focus:ring-2 ${
                  cookieCheck === null ? 'border-zinc-300 focus:ring-blue-400'
                    : cookieCheck.ok ? 'border-green-300 focus:ring-green-400'
                    : 'border-red-300 focus:ring-red-400'
                }`}
              />
              {cookieCheck && (
                <p className={`text-[11px] font-bold mb-2 ${cookieCheck.ok ? 'text-green-700' : 'text-red-600'}`}>
                  {cookieCheck.ok ? '✓ ' : '⚠ '}{cookieCheck.note}
                </p>
              )}
              <button
                onClick={importSession}
                disabled={busy || !cookieCheck?.ok}
                className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg font-bold text-sm hover:bg-blue-700 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? <Loader2 size={16} className="animate-spin" /> : '🔑'} Import Session
              </button>
              {importMsg && (
                <p className={`text-xs font-bold mt-2 ${importMsg.ok ? 'text-green-700' : 'text-red-600'}`}>
                  {importMsg.ok ? '✅ ' : '❌ '}{importMsg.text}
                </p>
              )}
              {loginWindowAvailable && !needsLogin && (
                <div className="mt-3">
                  <p className="text-[11px] text-zinc-500 text-center mb-2">— or, when the server runs on this computer —</p>
                  {loginWindowButton}
                </div>
              )}
              <div className="mt-3 pt-3 border-t border-zinc-100 space-y-1.5">
                <p className="text-[11px] text-zinc-500">
                  <span className="font-bold">🔒 Safe:</span> with session import your password is never involved. With the login window you type it into Facebook itself — only the resulting session is stored, encrypted on your own server, and you can replace or unlink it anytime.
                </p>
                <p className="text-[11px] text-zinc-500">
                  <span className="font-bold">🧭 Any browser:</span> it doesn't matter which browser you use for this dashboard. You only need to export cookies from a browser that's logged into facebook.com. Cookie-Editor supports Chrome, Edge, and Firefox. On <span className="font-bold">Safari</span>, either export using Chrome/Edge/Firefox, or use Safari's <span className="italic">Develop → Show Web Inspector → Storage → Cookies</span>.
                </p>
                <p className="text-[11px] text-zinc-500">
                  <span className="font-bold">↻ If scraping later says "login required":</span> your Facebook session expired or Facebook asked for a security check. Log into facebook.com, re-export and paste again — takes ~30 seconds.
                </p>
              </div>
            </div>
          )}
        </div>

        <button
          onClick={() => (embedded ? onClose?.() : window.close())}
          className="w-full flex items-center justify-center gap-2 px-6 py-3 bg-zinc-900 text-white rounded-xl font-bold text-sm hover:bg-black transition-all"
        >
          <X size={18} />
          Close
        </button>

        <p className="text-xs text-zinc-400 text-center mt-4">
          💡 Refresh your main dashboard to see newly synced posts
        </p>
      </div>
    </div>
  );
}
