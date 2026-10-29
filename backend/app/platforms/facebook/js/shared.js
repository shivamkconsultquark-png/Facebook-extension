/* Shared constants/helpers for the service worker, content scripts and popup.
 * Classic script: attaches FBShieldShared to globalThis (and module.exports under Node tests). */
(function (root) {
  'use strict';

  // Same bands as the Twitter page's % explainer (0–34 / 35–69 / 70–100) at "normal".
  const SENSITIVITY_THRESHOLDS = { low: [45, 80], normal: [35, 70], high: [25, 60] };

  // One vocabulary and palette for the in-feed badges, the popup and the dashboard
  // (dashboard mirrors these in src/theme.js).
  const CATEGORIES = {
    likely_legitimate: { label: 'Looks safe', short: 'Safe', color: '#15803d', bg: '#f0fdf4', border: '#bbf7d0', icon: 'check' },
    suspicious: { label: 'Suspicious', short: 'Suspicious', color: '#b45309', bg: '#fffbeb', border: '#fde68a', icon: 'alert' },
    likely_scam: { label: 'Likely scam', short: 'Scam', color: '#b91c1c', bg: '#fef2f2', border: '#fecaca', icon: 'stop' },
    unable_to_verify: { label: 'Not checked', short: 'Not checked', color: '#64748b', bg: '#f8fafc', border: '#e2e8f0', icon: 'dash' },
  };

  // 16px stroke icons (Lucide-style paths) shared by badge and popup.
  const ICONS = {
    shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
    check: 'M20 6 9 17l-5-5',
    alert: 'M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
    stop: 'M12 8v4M12 16h.01M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9z',
    dash: 'M5 12h14',
    spinner: 'M21 12a9 9 0 1 1-6.2-8.6',
  };

  const FILTER_MODES = {
    all: null,
    legitimate: 'likely_legitimate',
    suspicious: 'suspicious',
    scam: 'likely_scam',
  };

  const DEFAULT_PREFS = {
    enabled: true,
    filter_mode: 'all',
    hide_threshold: null,
    sensitivity: 'normal',
    warn_on_links: true,
  };

  const FACEBOOK_HOST_RE = /(^|\.)(facebook\.com|fb\.com|fb\.watch|messenger\.com)$/i;

  function categoryFor(score, sensitivity) {
    if (score === null || score === undefined) return 'unable_to_verify';
    const [suspicious, scam] = SENSITIVITY_THRESHOLDS[sensitivity] || SENSITIVITY_THRESHOLDS.normal;
    if (score >= scam) return 'likely_scam';
    if (score >= suspicious) return 'suspicious';
    return 'likely_legitimate';
  }

  function normaliseText(s) {
    return String(s || '')
      .normalize('NFKC')
      .replace(/[​-‏⁠﻿]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  /** Two independent FNV-1a 32-bit hashes -> 16 hex chars. Fast, sync, good enough for cache keys. */
  function fingerprint(str) {
    let h1 = 0x811c9dc5, h2 = 0x01000193 ^ 0x5bd1e995;
    for (let i = 0; i < str.length; i++) {
      const c = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ c, 0x01000193);
      h2 = Math.imul(h2 ^ c, 0x5bd1e995) ^ (h2 >>> 15);
    }
    return (h1 >>> 0).toString(16).padStart(8, '0') + (h2 >>> 0).toString(16).padStart(8, '0');
  }

  function safeUrl(href, base) {
    try { return new URL(href, base || 'https://www.facebook.com/'); } catch { return null; }
  }

  function isFacebookHost(host) {
    return FACEBOOK_HOST_RE.test(host || '');
  }

  /** Resolve l.facebook.com/l.php?u=... wrappers locally (no network). */
  function unwrapFacebookLink(href) {
    const u = safeUrl(href);
    if (!u) return null;
    if (/^(l|lm)\.facebook\.com$/i.test(u.hostname) && u.searchParams.get('u')) {
      const inner = safeUrl(u.searchParams.get('u'));
      if (inner) { inner.searchParams.delete('fbclid'); return inner.href; }
    }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    u.searchParams.delete('fbclid');
    return u.href;
  }

  function isExternalLink(href) {
    const unwrapped = unwrapFacebookLink(href);
    if (!unwrapped) return false;
    const u = safeUrl(unwrapped);
    return !!u && !isFacebookHost(u.hostname);
  }

  const api = {
    SENSITIVITY_THRESHOLDS, CATEGORIES, ICONS, FILTER_MODES, DEFAULT_PREFS,
    categoryFor, normaliseText, fingerprint, safeUrl, isFacebookHost, unwrapFacebookLink, isExternalLink,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FBShieldShared = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
