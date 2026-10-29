/* Injected by the server-side scraper (after shared.js + extractor.js) into the logged-in
 * Facebook home feed. Expands truncated posts, then returns the posts currently rendered.
 * Read-only apart from clicking Facebook's own "See more" text expanders. */
(function (root) {
  'use strict';
  const S = root.FBShieldShared;
  const X = root.FBShieldExtractor;

  /** Click "See more" expanders inside feed posts so the full text is in the DOM. */
  function expandPosts(posts) {
    let clicked = 0;
    for (const post of posts) {
      for (const btn of post.querySelectorAll('div[role="button"], span[role="button"]')) {
        const label = (btn.textContent || '').trim();
        if ((label === 'See more' || label === 'See More') && !btn.closest('[aria-label^="Comment"]')) {
          try { btn.click(); clicked++; } catch { /* ignore */ }
        }
      }
    }
    return clicked;
  }

  const CONTENT_IMG = /^https:\/\/scontent[^/]*\.fbcdn\.net\//;  // photo CDN (not emoji/icons on static.xx)

  const MAX_IMAGES = 10;

  /** Every photo of the post in order (multi-photo posts show up to 5 tiles), the "+N"
   *  count Facebook prints over the last tile, and whether the post is a photo/video/reel. */
  function postMedia(post, permalink) {
    const images = [];
    for (const img of post.querySelectorAll('img[src]')) {
      if (img.closest('h2, h3, h4, strong, [data-ad-rendering-role="profile_name"], [role="button"], [role="article"][aria-label^="Comment"]')) continue;
      const src = img.getAttribute('src') || '';
      if (!CONTENT_IMG.test(src) || images.includes(src)) continue;
      // Lazy-loaded photos can report naturalWidth 0, so also accept the layout size.
      // Grid tiles of a 4-5 photo post are ~200 px; avatars and icons are <= 40 px.
      const w = Math.max(img.naturalWidth || 0, parseInt(img.getAttribute('width'), 10) || 0,
                         img.getBoundingClientRect ? img.getBoundingClientRect().width : 0);
      if (w >= 120) images.push(src);
      if (images.length >= MAX_IMAGES) break;
    }
    // "+N" sits on the 5th tile and counts that tile too: the album has 4 + N photos.
    // `more` = photos not in `images` (the hidden 5th tile's image is already there).
    let more = 0;
    for (const el of post.querySelectorAll('div, span')) {
      const m = el.childElementCount === 0 && (el.textContent || '').trim().match(/^\+(\d{1,3})$/);
      if (m) { more = Math.max(0, 4 + parseInt(m[1], 10) - images.length); break; }
    }
    // Only a video that is actually shown: photo posts can carry a zero-size <video> placeholder.
    const video = [...post.querySelectorAll('video')].find((v) =>
      v.getAttribute('poster') || (v.getBoundingClientRect && v.getBoundingClientRect().width >= 100)) || null;
    const poster = video && video.getAttribute('poster');
    const link = permalink || '';
    const hasReelLink = /\/reel\//.test(link) || !!post.querySelector('a[href*="/reel/"]:not([href*="/reel/hashtag"])');
    // A multi-photo album can hold a video tile (or a hidden video element): it's still a photo post.
    const album = images.length >= 2;
    let type = null;
    if (/\/reel\//.test(link) || (video && hasReelLink && !images.length)) type = 'reel';
    else if (!album && (video || /\/(videos|watch)\//.test(link) || /[?&]v=\d/.test(link))) type = 'video';
    else if (images.length) type = 'photo';
    if (!images.length && poster && CONTENT_IMG.test(poster)) images.push(poster);
    return { images, more, type, albumUrl: more ? albumLink(post) : null, videoUrl: type ? videoLink(post, link) : null };
  }

  const stripTracking = (href) => {
    const u = S.safeUrl(href);
    if (!u || !S.isFacebookHost(u.hostname)) return null;
    for (const k of [...u.searchParams.keys()]) if (k.startsWith('__')) u.searchParams.delete(k);
    return `https://www.facebook.com${u.pathname}${u.search}`;
  };

  /** First photo tile of a multi-photo post: opens Facebook's photo viewer for this post's set. */
  function albumLink(post) {
    const a = post.querySelector('a[href*="/photo"][href*="set=pcb."], a[href*="/photo"][href*="set=a."]');
    return a ? stripTracking(a.getAttribute('href')) : null;
  }

  /** The video's own page (/reel/ID, /videos/ID, /watch/?v=ID): what Facebook's embedded player needs. */
  function videoLink(post, permalink) {
    if (/\/(reel|videos)\/\d+|[?&]v=\d+/.test(permalink || '')) return permalink;
    for (const a of post.querySelectorAll('a[href*="/reel/"], a[href*="/videos/"], a[href*="/watch/"]')) {
      const href = a.getAttribute('href') || '';
      if (/\/reel\/\d+|\/videos\/(?:[^/]+\/)?\d+|\/watch\/\?v=\d+/.test(href)) return stripTracking(href);
    }
    return null;
  }

  function authorImage(post) {
    // Profile pictures are small images (often SVG <image>) inside the post header.
    const svgImg = post.querySelector('svg image');
    const href = svgImg && (svgImg.getAttribute('xlink:href') || svgImg.getAttribute('href'));
    if (href && /^https:\/\//.test(href)) return href;
    const img = post.querySelector('a[role="link"] img, h2 img, h3 img, h4 img');
    const src = img && img.getAttribute('src');
    return src && /^https:\/\//.test(src) ? src : null;
  }

  /** Reaction / comment / share counts as Facebook displays them ("1.2K"), best effort. */
  const COUNT = /(\d[\d.,]*\s*[KkMm]?)/;

  /** The number Facebook prints inside the Like / Comment / Share button ("8.4K"). */
  function buttonCount(post, role) {
    const marker = post.querySelector(`[data-ad-rendering-role="${role}"]`);
    const btn = marker && marker.closest('[role="button"]');
    const m = btn && (btn.innerText || btn.textContent || '').match(COUNT);
    return m ? m[1].replace(/\s+/g, '') : null;
  }

  function counts(post) {
    const text = (post.innerText || post.textContent || '');
    const pick = (re) => { const m = text.match(re); return m ? m[1] : null; };
    let reactions = buttonCount(post, 'like_button');
    if (!reactions) {
      const r = post.querySelector('[aria-label^="All reactions"], [aria-label*="reactions" i], [aria-label^="Like: "]');
      const m = r && (r.getAttribute('aria-label') + ' ' + (r.textContent || '')).match(COUNT);
      if (m) reactions = m[1].replace(/\s+/g, '');
    }
    return {
      reactions,
      // Older layout: "12 comments 3 shares". innerText can glue spans, so no trailing \b.
      comments: buttonCount(post, 'comment_button') || pick(/(\d[\d.,]*[KkMm]?)\s+comments?(?![a-z])/i),
      shares: buttonCount(post, 'share_button') || pick(/(\d[\d.,]*[KkMm]?)\s+shares?(?![a-z])/i),
    };
  }

  /** Facebook fills in a post's timestamp link (its permalink) only when it is hovered.
   *  Dispatch the same hover event a mouse would, and expand "See more", so the next
   *  collect() reads the real post link and full text. Returns the number of posts touched. */
  root.FBShieldReveal = function reveal() {
    const posts = X.findPosts(document);
    for (const post of posts) {
      for (const a of post.querySelectorAll('a[href^="?__cft__"], a[href="#"], a[href^="/?__cft__"]')) {
        if ((a.getAttribute('aria-label') || '').startsWith('Hide')) continue;
        a.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
        a.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
      }
    }
    expandPosts(posts);
    return posts.length;
  };

  root.FBShieldCollect = function collect(maxPosts) {
    const posts = X.findPosts(document);
    const expanded = expandPosts(posts);
    const out = [];
    for (const el of posts) {
      const ex = X.extractPost(el);
      if (!ex || ex.empty) continue;
      const c = counts(el);
      const media = postMedia(el, ex.post.permalink);
      out.push({
        fingerprint: ex.fingerprint,
        post: ex.post,
        media_url: media.images[0] || null,
        media_urls: media.images,
        media_more: media.more,
        media_type: media.type,
        album_url: media.albumUrl,
        video_url: media.videoUrl,
        author_image: authorImage(el),
        reactions_count: c.reactions,
        comments_count: c.comments,
        shares_count: c.shares,
      });
      if (out.length >= maxPosts) break;
    }
    return { posts: out, expanded, found: posts.length };
  };
})(window);
