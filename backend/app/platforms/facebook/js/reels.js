/* Injected with shared.js + extractor.js + collect.js. On facebook.com/reel/… returns the
 * reels currently rendered (the playing one plus any preloaded), in the same shape as
 * FBShieldCollect so they are scored and stored like feed posts. Read-only. */
(function (root) {
  'use strict';
  const S = root.FBShieldShared;
  const X = root.FBShieldExtractor;
  const CONTENT_IMG = /^https:\/\/scontent[^/]*\.fbcdn\.net\//;
  const COUNT = /(\d[\d.,]*\s*[KkMm]?)/;

  /** The smallest ancestor of a [data-video-id] that also holds the reel's Like button. */
  function cardFor(videoEl) {
    let c = videoEl;
    for (let i = 0; i < 30 && c; i++, c = c.parentElement) {
      if (c.querySelector('[aria-label="Like"][role="button"]')) {
        return c.querySelectorAll('[data-video-id]').length === 1 ? c : null;
      }
    }
    return null;
  }

  function buttonCount(card, selector) {
    const btn = card.querySelector(selector);
    const m = btn && (btn.innerText || btn.textContent || '').match(COUNT);
    return m ? m[1].replace(/\s+/g, '') : null;
  }

  function caption(card) {
    const parts = [];
    for (const el of card.querySelectorAll('span[dir="auto"], div[dir="auto"]')) {
      if (el.closest('h2, [role="button"], [aria-hidden="true"]')) continue;
      if (el.parentElement && el.parentElement.closest('[dir="auto"]')) continue;
      const t = S.normaliseText(el.innerText || el.textContent || '');
      if (t.length < 2 || /^[\d.,\s]*[KkMm]?$/.test(t) || parts.some((p) => p.includes(t))) continue;
      parts.push(t);
    }
    return X.stripControls(parts.join('\n')).slice(0, 5000);
  }

  root.FBShieldCollectReels = function collectReels() {
    const out = [];
    for (const v of document.querySelectorAll('[data-video-id]')) {
      const id = v.getAttribute('data-video-id');
      const card = /^\d+$/.test(id || '') && cardFor(v);
      if (!card) continue;
      const owner = card.querySelector('a[aria-label$=", see owner profile"]');
      if (!owner) continue;  // preloaded card without its details yet
      const name = S.normaliseText(owner.textContent || '') ||
        owner.getAttribute('aria-label').replace(/, see owner profile$/, '');
      const text = caption(card);
      const urls = [];
      for (const a of card.querySelectorAll('a[href]')) {
        const abs = S.safeUrl(a.getAttribute('href'));
        if (abs && S.isExternalLink(abs.href)) {
          const u = S.unwrapFacebookLink(abs.href);
          if (u && !urls.includes(u)) urls.push(u);
        }
      }
      const video = card.querySelector('video[poster]');
      const poster = video && video.getAttribute('poster');
      const avatarEl = card.querySelector('a[aria-label="See owner profile"] image, svg image');
      const avatar = avatarEl && (avatarEl.getAttribute('xlink:href') || avatarEl.getAttribute('href'));
      out.push({
        fingerprint: S.fingerprint(`reel|${id}`),
        post: {
          post_id: id,
          permalink: `https://www.facebook.com/reel/${id}`,
          text,
          urls: urls.slice(0, 20),
          author_name: name.slice(0, 255) || null,
          author_url: X.cleanProfileUrl(owner.getAttribute('href')),
          is_sponsored: /(^|\n)Sponsored(\n|$)/.test(card.innerText || ''),
        },
        media_url: poster && CONTENT_IMG.test(poster) ? poster : null,
        media_urls: poster && CONTENT_IMG.test(poster) ? [poster] : [],
        media_more: 0,
        media_type: 'reel',
        video_url: `https://www.facebook.com/reel/${id}`,
        author_image: avatar && /^https:\/\//.test(avatar) ? avatar : null,
        reactions_count: buttonCount(card, '[aria-label="Like"][role="button"]'),
        comments_count: buttonCount(card, '[aria-label="Comment"][role="button"]'),
        // Not [aria-label^="Share"]: that also matches the "Shared with Public" privacy icon.
        shares_count: buttonCount(card, '[aria-label^="Share,"][role="button"], [aria-label="Share"][role="button"]'),
      });
    }
    return { posts: out };
  };
})(window);
