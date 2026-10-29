import React, { useState, useEffect, useRef } from 'react';
import { toZonedDate } from '../utils/timezone';
import axios from 'axios';
import { Facebook, ThumbsUp, MessageCircle, Share2, ExternalLink, RefreshCcw, Loader2, Zap, X as XIcon, Search, Info, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, Play, Film, Images } from 'lucide-react';
import { API_MAIN } from '../config/api';
import { useAuth } from '../context/AuthContext';
import FacebookScraper from './FacebookScraper';
import AdvancedDatePicker from '../components/dashboard/AdvancedDatePicker';
import { format } from 'date-fns';
import 'react-datepicker/dist/react-datepicker.css';
import './DashboardAnalytics.css'; // Reuse some styles
import { Skeleton, SkeletonHeader, SkeletonText } from '../components/common/Skeleton';

// The Facebook service can run inside the main API (same routers) or standalone.
export const API_FB = (import.meta.env && import.meta.env.VITE_FB_SHIELD_API) || API_MAIN;

// Date range check for the timeline filter (dates are 'yyyy-MM-dd', so string order = date order).
// Returns the message to show, or null when the range is fine. Either end may be empty.
export function dateRangeError(from, to) {
  const today = format(new Date(), 'yyyy-MM-dd');
  if (from && from > today) return "Start date can't be in the future.";
  if (to && to > today) return "End date can't be in the future.";
  if (from && to && from > to) return 'Start date must be on or before the end date.';
  return null;
}

// Full-screen viewer for a post's photos (arrow keys / Esc).
function MediaViewer({ urls, start, onClose }) {
  const [i, setI] = useState(start);
  const prev = () => setI((v) => (v - 1 + urls.length) % urls.length);
  const next = () => setI((v) => (v + 1) % urls.length);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft') prev();
      else if (e.key === 'ArrowRight') next();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
  return (
    <div className="fixed inset-0 z-[100] bg-black/90 flex items-center justify-center" onClick={onClose}>
      <img src={urls[i]} alt="" onClick={(e) => e.stopPropagation()} className="max-w-[92vw] max-h-[88vh] object-contain rounded-lg" />
      <button onClick={onClose} className="absolute top-4 right-4 p-2 rounded-full bg-white/10 text-white hover:bg-white/20" title="Close (Esc)"><XIcon size={20} /></button>
      {urls.length > 1 && (
        <>
          <button onClick={(e) => { e.stopPropagation(); prev(); }} className="absolute left-4 p-3 rounded-full bg-white/10 text-white hover:bg-white/20" title="Previous"><ChevronLeft size={24} /></button>
          <button onClick={(e) => { e.stopPropagation(); next(); }} className="absolute right-4 p-3 rounded-full bg-white/10 text-white hover:bg-white/20" title="Next"><ChevronRight size={24} /></button>
          <span className="absolute bottom-5 px-3 py-1 rounded-full bg-white/10 text-white text-xs font-bold">{i + 1} / {urls.length}</span>
        </>
      )}
    </div>
  );
}

// Facebook's own embeddable players (public videos and reels play without a Facebook login).
// With the video's own link: the video player. Otherwise the post embed, which plays the video inside it.
const embedUrl = (post, w, h) => (post.video_url
  ? `https://www.facebook.com/plugins/video.php?href=${encodeURIComponent(post.video_url)}&show_text=false&autoplay=true&width=${w}&height=${h}`
  : `https://www.facebook.com/plugins/post.php?href=${encodeURIComponent(post.permalink)}&show_text=false&width=${w}`);

// Facebook's player ignores autoplay=true in a plain iframe, so it waited for a second click.
// The SDK player can be told to play() once it is ready: one click starts it. Browsers only allow
// that muted; Facebook's volume button turns the sound on.
let fbSdk = null;
const loadFbSdk = () => {
  if (!fbSdk) {
    fbSdk = new Promise((resolve, reject) => {
      if (window.FB) { resolve(window.FB); return; }
      window.fbAsyncInit = () => {
        window.FB.init({ xfbml: false, version: 'v19.0' });
        resolve(window.FB);
      };
      const s = document.createElement('script');
      s.src = 'https://connect.facebook.net/en_US/sdk.js';
      s.async = true;
      s.crossOrigin = 'anonymous';
      s.onerror = () => { fbSdk = null; reject(new Error('Facebook player unavailable')); };
      document.body.appendChild(s);
    });
  }
  return fbSdk;
};

let videoSeq = 0;
function SdkVideo({ href, width, onFail }) {
  const box = useRef(null);
  const [id] = useState(() => `fbvid-${++videoSeq}`);
  useEffect(() => {
    let done = false;
    let unsub = null;
    // No player within 10 s (SDK blocked, video can't be embedded): use the plain iframe.
    const timer = setTimeout(() => { if (!done) onFail(); }, 10000);
    loadFbSdk().then((FB) => {
      if (!box.current) return;
      const ready = (msg) => {
        if (msg.type !== 'video' || msg.id !== id) return;
        done = true;
        clearTimeout(timer);
        msg.instance.mute();
        msg.instance.play();
      };
      FB.Event.subscribe('xfbml.ready', ready);
      unsub = () => FB.Event.unsubscribe('xfbml.ready', ready);
      FB.XFBML.parse(box.current);
    }).catch(() => { clearTimeout(timer); onFail(); });
    return () => { clearTimeout(timer); if (unsub) unsub(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div ref={box} className="w-full h-full flex items-center justify-center">
      <div id={id} className="fb-video" data-href={href} data-width={width} data-autoplay="true"
        data-allowfullscreen="true" data-show-text="false" data-show-captions="false" />
    </div>
  );
}

// A video or reel: thumbnail + play button; clicking it loads Facebook's player in place.
// Some videos only play on facebook.com (Facebook's player would show "Video unavailable"):
// the server checks first, and those get a "Watch on Facebook" card instead.
function PostVideo({ post, poster }) {
  const { token } = useAuth();
  const [state, setState] = useState('idle'); // idle | checking | playing | blocked
  const [sdkFailed, setSdkFailed] = useState(false);
  const reel = post.media_type === 'reel';
  const kind = reel ? 'reel' : 'video';
  // The post embed also shows the page header and counts, so it needs more height.
  const [w, h] = reel ? [280, 498] : post.video_url ? [560, 315] : [500, 560];
  const canEmbed = !!(post.video_url || post.permalink);
  const open = () => window.open(post.video_url || post.permalink || poster, '_blank');
  const thumb = poster
    ? <img src={poster} alt="" loading="lazy" className={`w-full object-cover ${reel ? 'aspect-[9/16]' : 'aspect-video'}`} />
    : <div className={reel ? 'aspect-[9/16]' : 'aspect-video'} />;

  const play = async () => {
    if (!canEmbed) { open(); return; }
    if (!post.video_url) { setState('playing'); return; }
    setState('checking');
    try {
      const res = await axios.get(`${API_FB}/facebook/feed/${post.id}/playable`, { headers: { Authorization: `Bearer ${token}` } });
      setState(res.data.playable === false ? 'blocked' : 'playing');
    } catch {
      setState('playing'); // couldn't check: just try the player
    }
  };

  if (state === 'blocked') {
    return (
      <div
        data-tile
        onClick={open}
        title={`Watch this ${kind} on Facebook`}
        className="relative mb-3 rounded-xl overflow-hidden border border-zinc-200 bg-zinc-900 cursor-pointer"
        style={{ width: '100%', maxWidth: w }}
      >
        <div className="opacity-30">{thumb}</div>
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-5 text-center">
          <p className="text-white text-sm font-bold">This {kind} can only be played on Facebook</p>
          <p className="text-white/70 text-[11px] leading-snug">Its owner or Facebook doesn't allow it to play on other sites.</p>
          <span className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white text-zinc-900 text-xs font-bold hover:bg-zinc-100">
            <ExternalLink size={13} /> Watch on Facebook
          </span>
        </div>
      </div>
    );
  }

  if (state === 'playing' && canEmbed) {
    return (
      <div className="mb-3">
        <div className="rounded-xl overflow-hidden border border-zinc-200 bg-black" style={{ width: '100%', maxWidth: w, aspectRatio: `${w} / ${h}` }}>
          {post.video_url && !sdkFailed
            ? <SdkVideo href={post.video_url} width={w} onFail={() => setSdkFailed(true)} />
            : (
              <iframe
                src={embedUrl(post, w, h)}
                title={`${reel ? 'Reel' : 'Video'} by ${post.user.name || 'Facebook page'}`}
                width="100%" height="100%" style={{ border: 'none', overflow: 'hidden' }} scrolling="no"
                allow="autoplay; clipboard-write; encrypted-media; picture-in-picture; web-share" allowFullScreen
              />
            )}
        </div>
        <p className="mt-1 text-[11px] text-zinc-400">
          Won't play? Some videos can't be embedded —{' '}
          <button onClick={open} className="font-bold text-blue-500 hover:underline">watch on Facebook</button>
        </p>
      </div>
    );
  }
  return (
    <div
      data-tile
      onClick={() => { if (state === 'idle') play(); }}
      title={canEmbed ? `Play this ${kind} here` : `Watch this ${kind} on Facebook`}
      className="relative mb-3 rounded-xl overflow-hidden border border-zinc-200 bg-zinc-900 cursor-pointer group/vid"
      style={{ width: '100%', maxWidth: w }}
    >
      {poster
        ? <img src={poster} alt="" loading="lazy" className={`w-full object-cover opacity-90 group-hover/vid:opacity-100 transition-opacity ${reel ? 'aspect-[9/16]' : 'aspect-video'}`} />
        : <div className={reel ? 'aspect-[9/16]' : 'aspect-video'} />}
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="w-14 h-14 rounded-full bg-black/55 flex items-center justify-center group-hover/vid:scale-110 transition-transform">
          {state === 'checking'
            ? <Loader2 size={26} className="text-white animate-spin" />
            : <Play size={26} className="text-white ml-1" fill="white" />}
        </span>
      </div>
      <span className="absolute top-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/60 text-white text-[10px] font-bold uppercase tracking-wide">
        <Film size={11} /> {reel ? 'Reel' : 'Video'}
      </span>
    </div>
  );
}

// Photo layouts as in the Facebook feed: 1 = full image, 2 = side by side,
// 3 = one tall + two, 4 = 2x2, 5+ = two on top + three below with "+N" on the last.
// Clicking any tile opens the viewer with every stored photo of the post.
const LAYOUTS = {
  2: { grid: 'grid-cols-2 grid-rows-1 h-[280px]', tile: () => '' },
  3: { grid: 'grid-cols-2 grid-rows-2 h-[420px]', tile: (i) => (i === 0 ? 'row-span-2' : '') },
  4: { grid: 'grid-cols-2 grid-rows-2 h-[420px]', tile: () => '' },
  5: { grid: 'grid-cols-6 grid-rows-[3fr_2fr] h-[440px]', tile: (i) => (i < 2 ? 'col-span-3' : 'col-span-2') },
};

function PostMedia({ post }) {
  const [viewer, setViewer] = useState(null);
  const urls = (post.media_urls && post.media_urls.length) ? post.media_urls : (post.media_url ? [post.media_url] : []);
  if (post.media_type === 'video' || post.media_type === 'reel') return <PostVideo post={post} poster={urls[0]} />;
  if (!urls.length) return null;
  const hide = (e) => { e.target.closest('[data-tile]').style.display = 'none'; };

  const shown = urls.slice(0, 5);
  const total = urls.length + (post.media_more || 0);
  // As on Facebook, the "+N" overlay covers the last tile, so N counts that tile too.
  const extra = total > shown.length ? total - (shown.length - 1) : 0;
  const layout = LAYOUTS[shown.length];
  return (
    <>
      {layout ? (
        <div className={`mb-3 grid gap-0.5 max-w-[560px] rounded-xl overflow-hidden border border-zinc-200 bg-zinc-50 ${layout.grid}`}>
          {shown.map((u, idx) => (
            <div key={u} data-tile onClick={() => setViewer(idx)}
              className={`relative cursor-zoom-in overflow-hidden ${layout.tile(idx)}`}>
              <img src={u} alt="" loading="lazy" onError={hide} className="w-full h-full object-cover hover:brightness-95 transition" />
              {idx === shown.length - 1 && extra > 0 && (
                <div className="absolute inset-0 bg-black/50 flex items-center justify-center text-white text-3xl font-bold">+{extra}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div data-tile onClick={() => setViewer(0)} className="mb-3 max-w-[560px] rounded-xl overflow-hidden border border-zinc-200 bg-zinc-50 flex justify-center cursor-zoom-in">
          <img src={urls[0]} alt="" loading="lazy" onError={hide} className="max-w-full max-h-[520px] w-auto h-auto object-contain" />
        </div>
      )}
      {total > 1 && (
        <p className="-mt-2 mb-3 flex items-center gap-1 text-[11px] text-zinc-400">
          <Images size={12} /> {total} photos
          {post.media_more ? ` · ${urls.length} saved, the rest on Facebook` : ' · click to view all'}
        </p>
      )}
      {viewer !== null && <MediaViewer urls={urls} start={viewer} onClose={() => setViewer(null)} />}
    </>
  );
}

export default function FacebookInsights() {
  const { token, user } = useAuth();
  const [feed, setFeed] = useState([]);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState(null); // why the last load failed; posts already shown stay visible
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [scraper, setScraper] = useState(null); // live browser-scraper status
  const [showScraper, setShowScraper] = useState(false); // in-page scraper panel
  const [spamFilter, setSpamFilter] = useState(null); // null | 'clean' | 'suspicious' | 'spam'
  const [totalCount, setTotalCount] = useState(0); // total rows for the current filter
  const [search, setSearch] = useState(''); // free-text search over post text/author
  // No date filter by default ("Select Range"): all stored posts, on first load and after Clear.
  const [dateFrom, setDateFrom] = useState(''); // YYYY-MM-DD
  const [dateTo, setDateTo] = useState(''); // YYYY-MM-DD (inclusive)
  const [maxScore, setMaxScore] = useState(''); // hide posts scoring >= this % (empty = off)
  const [sort, setSort] = useState('newest'); // newest | oldest | score_desc | score_asc
  const [mediaFilter, setMediaFilter] = useState(''); // '' (all) | posts | videos | reels
  const [dateError, setDateError] = useState(null); // why the last date pick was rejected
  const [showScoreInfo, setShowScoreInfo] = useState(false); // % score explainer popover
  // Stats & spam-analysis blocks collapsed by default so the timeline is
  // visible on first load; the choice is remembered per browser.
  const [showStats, setShowStats] = useState(() => {
    try { return localStorage.getItem('fbfilter_show_stats') === '1'; } catch { return false; }
  });
  const toggleStats = () => setShowStats((v) => {
    const next = !v;
    try { localStorage.setItem('fbfilter_show_stats', next ? '1' : '0'); } catch { /* ignore */ }
    return next;
  });

  // Poll the browser-scraper status so the header shows live scraping state.
  useEffect(() => {
    if (!token) return;
    const headers = { Authorization: `Bearer ${token}` };
    if (user?.company_id) headers['X-Company-Id'] = user.company_id;
    if (user?.table_prefix) headers['X-Table-Prefix'] = user.table_prefix;

    let cancelled = false;
    const poll = async () => {
      try {
        const res = await axios.get(`${API_FB}/facebook/browser-scraper/status`, { headers });
        if (!cancelled) setScraper(res.data);
      } catch {
        if (!cancelled) setScraper(null);
      }
    };
    poll();
    const timer = setInterval(poll, 10000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [token, user]);


  // withConfig: /config runs aggregate queries over the whole posts
  // table, so filter/page changes skip it — only the initial load and the
  // REFRESH DATA button re-fetch it.
  const fetchData = async (isRefresh = false, page = 1, overrides = {}, withConfig = !isRefresh) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);

    // Current filters, with explicit overrides for just-changed values
    // (state updates are async, so the new value is passed in directly).
    const f = { category: spamFilter, search, dateFrom, dateTo, maxScore, sort, media: mediaFilter, ...overrides };

    try {
      const headers = { Authorization: `Bearer ${token}` };

      // Fetch Feed with pagination + active filters (config in parallel if needed)
      const params = { page, limit: 10 };
      if (f.category) params.category = f.category;
      if (f.search && f.search.trim()) params.search = f.search.trim();
      if (f.dateFrom) params.date_from = f.dateFrom;
      if (f.dateTo) params.date_to = f.dateTo;
      if (f.maxScore !== '' && f.maxScore !== null) params.hide_above = f.maxScore;
      if (f.sort) params.sort = f.sort;
      if (f.media) params.media = f.media;
      const feedPromise = axios.get(`${API_FB}/facebook/feed`, { params, headers });
      const configPromise = withConfig ? axios.get(`${API_FB}/facebook/config`, { headers }) : null;

      const feedRes = await feedPromise;
      setFeed(feedRes.data.feed);
      setTotalPages(feedRes.data.total_pages);
      setTotalCount(feedRes.data.total || 0);
      setCurrentPage(page);

      if (configPromise) {
        const configRes = await configPromise;
        setConfig(configRes.data.config);
      }
      setLoadError(null);
    } catch (e) {
      console.error('Failed to fetch Facebook data:', e);
      setLoadError(e.response
        ? `The server couldn't load posts (HTTP ${e.response.status}).`
        : "Can't reach the Facebook Filter server. It may be restarting.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  // While loading fails (server down or restarting), retry every 15 s; the reads are safe to repeat.
  const fetchRef = useRef(fetchData);
  fetchRef.current = fetchData;
  useEffect(() => {
    if (!loadError) return undefined;
    const timer = setTimeout(() => fetchRef.current(true, currentPage), 15000);
    return () => clearTimeout(timer);
  }, [loadError, currentPage]);

  // Toggle the spam-category filter and reload the timeline from page 1.
  const applyFilter = (key) => {
    const next = spamFilter === key ? null : key;
    setSpamFilter(next);
    fetchData(true, 1, { category: next });
  };

  const anyFilterActive = spamFilter || search || dateFrom || dateTo || maxScore !== '' || sort !== 'newest' || mediaFilter;

  const clearFilters = () => {
    setSpamFilter(null); setSearch(''); setDateFrom(''); setDateTo('');
    setMaxScore(''); setSort('newest'); setMediaFilter(''); setDateError(null);
    fetchData(true, 1, { category: null, search: '', dateFrom: '', dateTo: '', maxScore: '', sort: 'newest', media: '' });
  };

  // Bottom pagination: load the page, then scroll back up so the new
  // page's first post is in view.
  const timelineRef = useRef(null);
  const gotoPage = (page) => {
    fetchData(true, page);
    timelineRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Debounced reload for the typed search filter — skips the initial mount
  // so it doesn't double-fetch with the load effect.
  const typedFilterMounted = useRef(false);
  useEffect(() => {
    if (!typedFilterMounted.current) { typedFilterMounted.current = true; return; }
    const t = setTimeout(() => fetchData(true, 1), 500);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    fetchData();
  }, [token]);

  if (loading) {
    return (
      <div className="p-8 w-full">
        <SkeletonHeader />
        <div className="flex flex-col gap-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="skl-card flex gap-4">
              <Skeleton width="44px" height={44} rounded />
              <div className="flex-1 flex flex-col gap-3">
                <Skeleton width="180px" height={12} />
                <SkeletonText lines={3} lastWidth="55%" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="px-8 pt-5 pb-8 w-full">
      {/* In-page Auto-Scraper panel (same as the Twitter page) */}
      {showScraper && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 backdrop-blur-sm py-8 px-4"
          onClick={() => setShowScraper(false)}
        >
          <div className="relative" onClick={(e) => e.stopPropagation()}>
            <button
              onClick={() => setShowScraper(false)}
              aria-label="Close"
              className="absolute -top-3 -right-3 z-10 w-9 h-9 flex items-center justify-center bg-white text-zinc-700 rounded-full shadow-lg border border-zinc-200 hover:bg-zinc-100 transition-all"
            >
              <XIcon size={18} />
            </button>
            <FacebookScraper embedded onClose={() => setShowScraper(false)} />
          </div>
        </div>
      )}

      <div className="flex justify-between items-center mb-5">
        <div>
          <h1 className="text-2xl font-black text-zinc-900 tracking-tightest flex items-center gap-3">
            Facebook <span className="text-blue-500">Filter</span>
            {scraper?.running ? (
              <span className="flex items-center gap-1 bg-green-50 border border-green-200 px-3 py-1 rounded-full text-xs font-bold text-green-700">
                <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
                {scraper.needs_login ? 'Waiting for Facebook login' : 'Scraping'}
              </span>
            ) : (
              <span className="flex items-center gap-1 bg-zinc-100 border border-zinc-200 px-3 py-1 rounded-full text-xs font-bold text-zinc-500">
                <span className="w-2 h-2 bg-zinc-400 rounded-full"></span>
                Scraper stopped
              </span>
            )}
          </h1>
          <p className="text-zinc-500 mt-1 text-sm">
            Social intelligence and account management.
            {scraper?.fb_account && (
              <span className="text-xs text-zinc-400"> Facebook account: <span className="font-bold">{scraper.fb_account}</span></span>
            )}
            {scraper?.running && scraper?.last_run && (
              <span className="text-xs text-zinc-400"> · Last scrape: {new Date(scraper.last_run).toLocaleTimeString()} · {scraper.last_count} posts</span>
            )}
          </p>
        </div>
        <div className="flex gap-2.5">
          <button
            onClick={toggleStats}
            title={showStats ? 'Hide stats & spam analysis' : 'Show stats & spam analysis'}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-xl font-bold text-xs border transition-all ${showStats ? 'bg-zinc-100 text-zinc-700 border-zinc-200' : 'bg-white text-zinc-500 border-zinc-200 hover:bg-zinc-50'}`}
          >
            {showStats ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            <span>STATS</span>
          </button>
          <button
            onClick={() => setShowScraper(true)}
            className={`flex items-center gap-1.5 px-4 py-2 text-white rounded-xl font-bold text-xs transition-all shadow-md ${scraper?.running ? 'bg-green-500 hover:bg-green-600' : 'bg-purple-500 hover:bg-purple-600'}`}
          >
            <Zap size={14} />
            <span>{scraper?.running ? 'SCRAPER RUNNING' : 'START SCRAPER'}</span>
          </button>
          <button
            onClick={() => fetchData(true, currentPage, {}, true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-4 py-2 bg-zinc-900 text-white rounded-xl font-bold text-xs hover:bg-black transition-all shadow-md disabled:opacity-50"
          >
            {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCcw size={14} />}
            <span>REFRESH DATA</span>
          </button>
        </div>
      </div>

      {showStats && (<>
      {/* Stats strip — compact tabular summary */}
      {config && (
        <div className="bg-white rounded-2xl border border-zinc-100 shadow-sm mb-8 grid grid-cols-2 sm:grid-cols-5 divide-y sm:divide-y-0 sm:divide-x divide-zinc-100 overflow-hidden">
          {[
            { label: 'Total Posts', value: config.total_posts || 0, color: 'text-zinc-900' },
            { label: 'Videos & Reels', value: config.total_videos || 0, color: 'text-zinc-900' },
            { label: 'Accounts', value: config.unique_authors || 0, color: 'text-zinc-900' },
            { label: 'Clean', value: config.spam_clean || 0, color: 'text-emerald-600' },
            { label: 'Spam', value: (config.spam_suspicious || 0) + (config.spam_spam || 0), color: 'text-rose-600' },
          ].map(({ label, value, color }) => (
            <div key={label} className="flex items-center justify-between gap-3 px-4 py-2.5">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">{label}</span>
              <span className={`text-sm font-black ${color}`}>{value.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}

      {/* Sync + spam summary */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 mb-8">
          {/* Sync Info */}
          <div className="bg-white p-6 rounded-2xl border border-zinc-100 shadow-sm">
            <h3 className="text-xs font-black text-zinc-400 uppercase tracking-widest mb-4">Sync Details</h3>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-xs text-zinc-500">Status</span>
                {scraper?.running ? (
                  <span className="flex items-center gap-1.5 text-xs font-bold text-emerald-600">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                    {scraper.needs_login ? 'Waiting for login' : 'Scraping live'}
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-xs font-bold text-zinc-500">
                    <span className="w-1.5 h-1.5 rounded-full bg-zinc-300"></span> Stopped
                  </span>
                )}
              </div>
              {config?.last_synced_at && (
                <div className="flex justify-between items-center">
                  <span className="text-xs text-zinc-500">Last Synced</span>
                  <span className="text-xs font-bold text-zinc-700">{config.last_synced_at}</span>
                </div>
              )}
              <div className="flex justify-between items-center">
                <span className="text-xs text-zinc-500">Source</span>
                <span className="text-xs font-bold text-zinc-700">Facebook Auto-Scraper</span>
              </div>
              {scraper?.fb_account && (
                <div className="flex justify-between items-center">
                  <span className="text-xs text-zinc-500">Facebook Account</span>
                  <span className="text-xs font-bold text-zinc-700">{scraper.fb_account}</span>
                </div>
              )}
              {scraper?.running && scraper?.total_scraped > 0 && (
                <div className="flex justify-between items-center">
                  <span className="text-xs text-zinc-500">Scraped this session</span>
                  <span className="text-xs font-bold text-zinc-700">{scraper.total_scraped.toLocaleString()} posts</span>
                </div>
              )}
            </div>
          </div>

          {/* Spam Breakdown */}
          {config && config.total_posts > 0 && (
            <div className="bg-white p-6 rounded-2xl border border-zinc-100 shadow-sm">
              <h3 className="text-xs font-black text-zinc-400 uppercase tracking-widest mb-4">
                Spam Analysis <span className="normal-case tracking-normal font-normal text-zinc-300">· click to filter</span>
              </h3>
              <div className="space-y-1">
                {[
                  { key: 'clean', label: 'Clean', count: config.spam_clean || 0, color: 'bg-emerald-500', textColor: 'text-emerald-600' },
                  { key: 'suspicious', label: 'Suspicious', count: config.spam_suspicious || 0, color: 'bg-amber-500', textColor: 'text-amber-600' },
                  { key: 'spam', label: 'Spam', count: config.spam_spam || 0, color: 'bg-rose-500', textColor: 'text-rose-600' },
                ].map(({ key, label, count, color, textColor }) => {
                  const pct = config.total_posts > 0 ? Math.round((count / config.total_posts) * 100) : 0;
                  const active = spamFilter === key;
                  return (
                    <div
                      key={key}
                      onClick={() => applyFilter(key)}
                      title={active ? 'Clear filter' : `Show only ${label.toLowerCase()} posts`}
                      className={`p-2 -mx-2 rounded-lg cursor-pointer transition-colors ${active ? 'bg-zinc-100 ring-1 ring-zinc-200' : 'hover:bg-zinc-50'}`}
                    >
                      <div className="flex justify-between items-center mb-1">
                        <span className={`text-xs ${active ? 'font-bold text-zinc-800' : 'text-zinc-500'}`}>{label}</span>
                        <span className={`text-xs font-bold ${textColor}`}>{count} ({pct}%)</span>
                      </div>
                      <div className="h-1.5 bg-zinc-100 rounded-full overflow-hidden">
                        <div className={`h-full ${color} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }}></div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
      </div>
      </>)}

      {/* Feed Section */}
      <div className="space-y-6">
          <div ref={timelineRef} className="bg-white rounded-2xl border border-zinc-200 shadow-sm scroll-mt-4">
            <div className="flex justify-between items-center gap-4 px-6 py-4 border-b border-zinc-100">
              <h3 className="text-lg font-black text-zinc-900 flex items-center gap-2">
                Timeline
                <span className="relative">
                  <button
                    onClick={() => setShowScoreInfo(v => !v)}
                    title="What does the % score mean?"
                    className={`w-6 h-6 flex items-center justify-center rounded-full border transition-colors ${showScoreInfo ? 'bg-zinc-900 text-white border-zinc-900' : 'bg-white text-zinc-400 border-zinc-200 hover:text-zinc-600'}`}
                  >
                    <Info size={13} />
                  </button>
                  {showScoreInfo && (
                    <div className="absolute left-0 top-8 z-50 w-80 bg-zinc-900 text-white rounded-xl p-4 shadow-2xl text-xs font-normal leading-relaxed text-left">
                      <p className="font-bold mb-2">What the % score means</p>
                      <p className="text-zinc-300 mb-3">
                        Every post is scored by AI with a scam probability from 0–100%.
                        Higher means more likely to be a scam, phishing or fraud attempt.
                      </p>
                      <div className="space-y-1.5">
                        <p><span className="inline-block w-16 font-bold text-emerald-400">0–34%</span> Clean — genuine posts, news, discussion</p>
                        <p><span className="inline-block w-16 font-bold text-amber-400">35–69%</span> Suspicious — worth a closer look</p>
                        <p><span className="inline-block w-16 font-bold text-rose-400">70–100%</span> Spam — giveaways, scams, fake recovery, phishing</p>
                      </div>
                      <p className="text-zinc-400 mt-3">Hover a post's % badge to see the reason behind its score.</p>
                    </div>
                  )}
                </span>
                {spamFilter && (
                  <button
                    onClick={() => applyFilter(spamFilter)}
                    title="Clear filter"
                    className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full bg-blue-50 text-blue-600 border border-blue-100 hover:bg-blue-100 transition-colors"
                  >
                    {spamFilter} <XIcon size={12} />
                  </button>
                )}
              </h3>
              <div className="flex items-center gap-3">
                <span className="text-[10px] font-bold px-3 py-1 bg-zinc-50 text-zinc-400 rounded-full border border-zinc-100 uppercase tracking-widest">
                  {feed.length > 0 ? `${feed.length} of ${totalCount.toLocaleString()}` : 'No Data'}
                </span>
                {totalPages > 1 && (
                  <div className="flex items-center gap-0.5 bg-zinc-50 border border-zinc-100 rounded-full px-1 py-0.5">
                    <button
                      onClick={() => fetchData(true, currentPage - 1)}
                      disabled={currentPage === 1 || refreshing}
                      title="Previous page"
                      className="w-7 h-7 flex items-center justify-center rounded-full text-zinc-500 hover:bg-white hover:shadow-sm disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                    >
                      <ChevronLeft size={15} />
                    </button>
                    <span className="text-[11px] font-bold text-zinc-500 px-1.5 whitespace-nowrap">
                      {currentPage} / {totalPages.toLocaleString()}
                    </span>
                    <button
                      onClick={() => fetchData(true, currentPage + 1)}
                      disabled={currentPage === totalPages || refreshing}
                      title="Next page"
                      className="w-7 h-7 flex items-center justify-center rounded-full text-zinc-500 hover:bg-white hover:shadow-sm disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                    >
                      <ChevronRight size={15} />
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* Filter toolbar — sticks to the top while scrolling the feed */}
            <div className="sticky top-0 z-20 flex flex-wrap items-center gap-2 px-6 py-3 bg-white/95 backdrop-blur border-b border-zinc-100">
              <div className="relative flex-1 min-w-[200px]">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search text or page name..."
                  className="w-full h-9 pl-9 pr-3 text-xs font-medium bg-zinc-50 border border-zinc-200 rounded-lg focus:outline-none focus:bg-white focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition-colors"
                />
              </div>
              <div className={`bg-zinc-50 border rounded-lg hover:bg-white transition-colors ${dateError ? 'border-rose-300 ring-2 ring-rose-100' : 'border-zinc-200'}`}>
                <AdvancedDatePicker
                  compact
                  label="Date Range"
                  startDate={dateFrom ? new Date(`${dateFrom}T00:00:00`) : null}
                  endDate={dateTo ? new Date(`${dateTo}T00:00:00`) : null}
                  maxDate={new Date()}
                  onChange={([start, end]) => {
                    const f = start ? format(start, 'yyyy-MM-dd') : '';
                    const t = end ? format(end, 'yyyy-MM-dd') : '';
                    const err = dateRangeError(f, t);
                    setDateError(err);
                    if (err) return; // keep the last valid range; the picker snaps back to it
                    setDateFrom(f);
                    setDateTo(t);
                    fetchData(true, 1, { dateFrom: f, dateTo: t });
                  }}
                />
              </div>
              <select
                value={maxScore}
                onChange={(e) => { setMaxScore(e.target.value); fetchData(true, 1, { maxScore: e.target.value }); }}
                title="Hide posts whose spam score is at or above this percentage"
                className={`h-9 px-3 text-xs font-semibold border rounded-lg focus:outline-none cursor-pointer transition-colors ${maxScore !== '' ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-zinc-50 text-zinc-600 border-zinc-200 hover:bg-white'}`}
              >
                <option value="">Hide: off</option>
                {[10, 20, 30, 40, 50, 60, 70, 80, 90].map((v) => (
                  <option key={v} value={v}>Hide ≥ {v}%</option>
                ))}
              </select>
              <select
                value={mediaFilter}
                onChange={(e) => { setMediaFilter(e.target.value); fetchData(true, 1, { media: e.target.value }); }}
                title="Show only posts, or only videos and reels"
                className={`h-9 px-3 text-xs font-semibold border rounded-lg focus:outline-none cursor-pointer transition-colors ${mediaFilter ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-zinc-50 text-zinc-600 border-zinc-200 hover:bg-white'}`}
              >
                <option value="">All types</option>
                <option value="posts">Posts &amp; photos</option>
                <option value="videos">Videos &amp; reels</option>
                <option value="reels">Reels only</option>
              </select>
              <select
                value={sort}
                onChange={(e) => { setSort(e.target.value); fetchData(true, 1, { sort: e.target.value }); }}
                className="h-9 px-3 text-xs font-semibold bg-zinc-50 text-zinc-600 border border-zinc-200 rounded-lg focus:outline-none cursor-pointer hover:bg-white transition-colors"
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="score_desc">Score: high → low</option>
                <option value="score_asc">Score: low → high</option>
              </select>
              {anyFilterActive && (
                <button
                  onClick={clearFilters}
                  className="flex items-center gap-1 h-9 px-3 text-xs font-bold text-rose-500 bg-rose-50 border border-rose-200 rounded-lg hover:bg-rose-100 transition-colors"
                >
                  <XIcon size={12} /> Clear
                </button>
              )}
              {dateError && (
                <p role="alert" className="basis-full flex items-center gap-1.5 text-[11px] font-semibold text-rose-600">
                  <Info size={12} /> {dateError}
                </p>
              )}
              {loadError && (
                <p role="alert" className="basis-full flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-[11px] font-semibold text-amber-800">
                  <Info size={12} className="shrink-0" />
                  <span>{loadError} Retrying automatically{feed.length ? '; the posts below may be out of date' : ''}.</span>
                  <button onClick={() => fetchData(true, currentPage)} className="ml-auto font-bold text-amber-900 hover:underline">Retry now</button>
                </p>
              )}
            </div>

            <div className="relative p-6">
              {refreshing && (
                <div className="absolute inset-0 z-10 flex items-start justify-center bg-white/50 rounded-xl">
                  <div className="sticky top-1/3 mt-16 flex items-center gap-2 px-4 py-2 bg-white rounded-full shadow-lg border border-zinc-200">
                    <Loader2 size={15} className="animate-spin text-blue-500" />
                    <span className="text-xs font-bold text-zinc-600">Loading posts…</span>
                  </div>
                </div>
              )}
            <div className={`space-y-4 transition-opacity duration-200 ${refreshing ? 'opacity-50 pointer-events-none' : ''}`}>
              {feed.length > 0 ? (
                <>
                  {feed.map((post) => (
                    <div key={post.id} className="group p-5 bg-zinc-50/70 rounded-xl border border-transparent hover:border-blue-100 hover:bg-white hover:shadow-sm transition-all duration-200">
                      <div className="flex gap-3.5">
                        {post.user.profile_image_url ? (
                          <img
                            src={post.user.profile_image_url}
                            alt=""
                            onError={(e) => { e.target.style.visibility = 'hidden'; }}
                            className="w-10 h-10 rounded-full border border-zinc-200 shadow-sm shrink-0 object-cover"
                          />
                        ) : (
                          <div className="w-10 h-10 rounded-full border border-zinc-200 shadow-sm shrink-0 bg-blue-50 text-blue-500 font-black flex items-center justify-center">
                            {(post.user.name || '?').trim().charAt(0).toUpperCase()}
                          </div>
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-start justify-between gap-2 mb-1.5">
                            <div className="flex items-center gap-2 min-w-0">
                              <button
                                onClick={() => post.user.url && window.open(post.user.url, '_blank')}
                                title={post.user.url ? `Open ${post.user.name} on Facebook` : undefined}
                                className="flex items-center gap-2 min-w-0 hover:underline decoration-zinc-300 underline-offset-2"
                              >
                                <span className="font-bold text-sm text-zinc-900 truncate">{post.user.name || 'Unknown'}</span>
                              </button>
                              <span className="text-zinc-300 shrink-0">·</span>
                              <span className="text-xs text-zinc-400 shrink-0" title="When the scraper first saw this post">
                                {toZonedDate(new Date(post.created_at), 'en-US', { month: 'short', day: 'numeric' })}
                              </span>
                            </div>
                            {post.spam_score !== undefined && post.spam_score !== null && (
                              <div
                                className={`relative group/spam shrink-0 px-2 py-0.5 rounded-md text-[10px] font-bold cursor-help ${
                                  post.spam_score >= 70
                                    ? 'bg-rose-50 text-rose-500'
                                    : post.spam_score >= 35
                                    ? 'bg-amber-50 text-amber-500'
                                    : 'bg-emerald-50 text-emerald-500'
                                }`}
                              >
                                {post.spam_score}%
                                <div className="absolute bottom-full right-0 mb-2 w-56 bg-zinc-900 text-white text-[11px] rounded-lg p-3 hidden group-hover/spam:block z-50 shadow-xl text-left font-normal leading-relaxed">
                                  <p className="font-bold mb-1">{post.spam_score >= 70 ? 'Spam' : post.spam_score >= 35 ? 'Suspicious' : 'Clean'}</p>
                                  <p className="text-zinc-400">{post.spam_reason || 'AI-analyzed'}</p>
                                </div>
                              </div>
                            )}
                          </div>
                          <p className="text-zinc-700 text-sm leading-relaxed mb-3 whitespace-pre-wrap">
                            {post.text}
                          </p>
                          <PostMedia post={post} />
                          <div className="flex items-center gap-6 text-zinc-400">
                            {post.reactions_count && (
                              <div className="flex items-center gap-1.5 text-xs hover:text-blue-500 transition-colors" title="Reactions">
                                <ThumbsUp size={14} />
                                <span className="font-bold">{post.reactions_count}</span>
                              </div>
                            )}
                            {post.comments_count && (
                              <div className="flex items-center gap-1.5 text-xs hover:text-blue-500 transition-colors" title="Comments">
                                <MessageCircle size={14} />
                                <span className="font-bold">{post.comments_count}</span>
                              </div>
                            )}
                            {post.shares_count && (
                              <div className="flex items-center gap-1.5 text-xs hover:text-blue-500 transition-colors" title="Shares">
                                <Share2 size={14} />
                                <span className="font-bold">{post.shares_count}</span>
                              </div>
                            )}
                            {post.permalink && (
                              <button
                                onClick={() => window.open(post.permalink, '_blank')}
                                className="flex items-center gap-1.5 text-xs hover:text-zinc-600 transition-colors ml-auto"
                              >
                                <ExternalLink size={14} />
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}

                  {/* Pagination */}
                  {totalPages > 1 && (
                    <div className="flex items-center justify-between pt-6 border-t border-zinc-100">
                      <button
                        onClick={() => gotoPage(currentPage - 1)}
                        disabled={currentPage === 1 || refreshing}
                        className="flex items-center gap-1.5 px-5 py-2 bg-white border border-zinc-200 rounded-lg text-xs font-bold text-zinc-600 hover:bg-zinc-50 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                      >
                        <ChevronLeft size={14} /> Previous
                      </button>
                      <span className="text-xs font-bold text-zinc-400">
                        {currentPage} / {totalPages.toLocaleString()}
                      </span>
                      <button
                        onClick={() => gotoPage(currentPage + 1)}
                        disabled={currentPage === totalPages || refreshing}
                        className="flex items-center gap-1.5 px-5 py-2 bg-white border border-zinc-200 rounded-lg text-xs font-bold text-zinc-600 hover:bg-zinc-50 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                      >
                        Next <ChevronRight size={14} />
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <div className="py-16 text-center">
                  <div className="w-16 h-16 bg-zinc-50 rounded-full flex items-center justify-center mx-auto mb-4">
                    <Facebook size={28} className="text-zinc-300" />
                  </div>
                  <p className="text-sm font-bold text-zinc-400 mb-1">{loadError ? "Posts couldn't be loaded" : 'No posts found'}</p>
                  {loadError ? (
                    <p className="text-xs text-zinc-400">{loadError}</p>
                  ) : anyFilterActive ? (
                    <p className="text-xs text-zinc-400">No posts match your filters. <button onClick={clearFilters} className="font-bold text-blue-500 hover:underline">Clear filters</button></p>
                  ) : (
                    <p className="text-xs text-zinc-400">Use the <strong>START SCRAPER</strong> button to scrape posts from Facebook</p>
                  )}
                </div>
              )}
            </div>
            </div>
          </div>
      </div>
    </div>
  );
}
