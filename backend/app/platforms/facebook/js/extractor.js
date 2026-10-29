/* Facebook feed post discovery + signal extraction. Pure DOM reads — never clicks,
 * expands, or otherwise interacts with Facebook on the user's behalf.
 *
 * Facebook's markup is obfuscated and changes often, so detection relies on semantic /
 * ARIA hooks (role, aria-posinset, data-ad-* roles) in priority order rather than class
 * names, and every lookup degrades gracefully. Update SELECTORS here when FB changes. */
(function (root) {
  'use strict';
  const S = (typeof module !== 'undefined' && module.exports) ? require('./shared.js') : root.FBShieldShared;

  const SELECTORS = {
    // Candidate post roots, most specific first.
    // Since 2026 the home feed has no role="feed" wrapper; each post is an [aria-posinset] item.
    postRoots: [
      '[role="feed"] [aria-posinset]',
      '[role="main"] [aria-posinset]',
      '[aria-posinset]',
      '[data-pagelet^="FeedUnit"]',
      '[role="article"]',
    ],
    // Hidden decoy / screen-reader text Facebook mixes into posts (e.g. "Facebook" x30,
    // scrambled link-preview titles). Never part of what the reader sees.
    hidden: '[aria-hidden="true"], [hidden], [data-ad-rendering-role="title"], [data-ad-rendering-role="description"], [data-ad-rendering-role="meta"]',
    // Feed units that are not posts (carousels of suggestions).
    nonPostAuthors: /^(reels|reels and short videos|stories|people you may know|suggested for you|groups you might like|suggested groups|follow pages|sponsored|no more posts)$/i,
    // Post body text, if FB exposes the message container.
    message: [
      '[data-ad-rendering-role="story_message"]',
      '[data-ad-comet-preview="message"]',
      '[data-ad-preview="message"]',
    ],
    author: [
      '[data-ad-rendering-role="profile_name"] a[href]',
      'h2 a[href]', 'h3 a[href]', 'h4 a[href]',
      'strong a[href]',
    ],
    authorText: ['[data-ad-rendering-role="profile_name"]', 'h2', 'h3', 'h4', 'strong'],
    // Comment sections / comment articles — excluded from post text and links.
    comments: ['[role="article"][aria-label^="Comment"]', '[aria-label*="comment" i][role="article"]', 'ul [role="article"]'],
  };

  const PERMALINK_PATTERNS = [
    /\/groups\/[^/]+\/(?:posts|permalink)\/(\d+)/,
    /\/posts\/(pfbid[\w]+|\d+)/,
    /[?&]story_fbid=(pfbid[\w]+|\d+)/,
    /\/permalink\/(\d+)/,
    /\/videos\/(?:[^/]+\/)?(\d+)/,
    /\/reel\/(\d+)/,
    /[?&]fbid=(\d+)/,
    /\/photos\/[^/]+\/(\d+)/,
  ];

  const MAX_TEXT = 5000;
  const MIN_SIGNAL_CHARS = 2;

  function textOf(el) {
    if (!el) return '';
    const t = (typeof el.innerText === 'string') ? el.innerText : el.textContent;
    return S.normaliseText(t);
  }

  function isComment(el) {
    return SELECTORS.comments.some((sel) => { try { return el.matches(sel); } catch { return false; } });
  }

  /** Nodes inside `post` that belong to comments (nested articles) rather than the post.
   *  When the post root is an aria-posinset wrapper, its first article is the post itself. */
  function insideNestedArticle(node, post) {
    const allowed = post.getAttribute('role') === 'article' ? 0 : 1;
    let articles = 0;
    let n = node.parentElement;
    while (n && n !== post) {
      if (n.getAttribute && n.getAttribute('role') === 'article') {
        // The 2026 layout has no post article, so a comment preview can be the only article.
        if (isComment(n)) return true;
        articles++;
      }
      n = n.parentElement;
    }
    if (node !== post && node.getAttribute && node.getAttribute('role') === 'article' && isComment(node)) return true;
    if (node.getAttribute && node.getAttribute('role') === 'article' && node !== post) articles++;
    return articles > allowed;
  }

  /** Find top-level feed post elements under `scope` (document or a mutated subtree). */
  function findPosts(scope) {
    const found = new Set();
    for (const sel of SELECTORS.postRoots) {
      let nodes;
      try { nodes = scope.querySelectorAll(sel); } catch { continue; }
      for (const el of nodes) {
        if (isComment(el)) continue;
        // aria-posinset is also used by menus/lists in the page chrome.
        if (el.closest('[role="navigation"], [role="banner"], [role="complementary"], [role="listbox"], [role="menu"], [role="dialog"]')) continue;
        // Skip articles nested in another candidate article (comments, shared-post embeds).
        const outerArticle = el.parentElement && el.parentElement.closest('[role="article"]');
        if (outerArticle && !el.hasAttribute('aria-posinset')) continue;
        found.add(el);
      }
      if (scope.matches && scope.matches(sel) && !isComment(scope)) found.add(scope);
    }
    // Keep only outermost candidates: an aria-posinset wrapper usually contains the article.
    const list = [...found];
    return list.filter((el) => !list.some((other) => other !== el && other.contains(el)));
  }

  function firstMatch(scope, selectors) {
    for (const sel of selectors) {
      let el;
      try { el = scope.querySelector(sel); } catch { el = null; }
      if (el) return el;
    }
    return null;
  }

  function cleanProfileUrl(href) {
    const u = S.safeUrl(href);
    if (!u || !S.isFacebookHost(u.hostname)) return null;
    const keep = new URLSearchParams();
    if (u.pathname === '/profile.php' && u.searchParams.get('id')) keep.set('id', u.searchParams.get('id'));
    const q = keep.toString();
    return `https://www.facebook.com${u.pathname}${q ? `?${q}` : ''}`;
  }

  function extractAuthor(post) {
    const a = firstMatch(post, SELECTORS.author);
    if (a && !insideNestedArticle(a, post)) {
      return { name: textOf(a).slice(0, 255) || null, url: cleanProfileUrl(a.getAttribute('href')) };
    }
    const t = firstMatch(post, SELECTORS.authorText);
    return { name: t ? textOf(t).slice(0, 255) || null : null, url: null };
  }

  function extractText(post, authorName) {
    const msgs = [];
    // Translated posts render the message block more than once (translation, original,
    // nested copies); keep each distinct visible text once.
    const addMsg = (t) => {
      if (!t || msgs.some((m) => m.includes(t))) return;
      for (let i = msgs.length - 1; i >= 0; i--) if (t.includes(msgs[i])) msgs.splice(i, 1);
      msgs.push(t);
    };
    for (const sel of SELECTORS.message) {
      for (const el of post.querySelectorAll(sel)) {
        if (insideNestedArticle(el, post) || el.closest(SELECTORS.hidden)) continue;
        if (el.parentElement && el.parentElement.closest(sel)) continue;
        if (el.getClientRects && el.isConnected && el.getClientRects().length === 0) continue;
        addMsg(stripControls(textOf(el)));
      }
      if (msgs.length) break;
    }
    if (!msgs.length) {
      // Fallback: user-authored text blocks are rendered with dir="auto".
      const seen = new Set();
      for (const el of post.querySelectorAll('div[dir="auto"], span[dir="auto"]')) {
        if (insideNestedArticle(el, post) || el.closest(SELECTORS.hidden) || el.closest('[role="button"]')) continue;
        // Not rendered (display:none decoys, the untranslated original behind "See original").
        if (el.getClientRects && el.isConnected && el.getClientRects().length === 0) continue;
        if (el.parentElement && el.parentElement.closest('div[dir="auto"]') && el.parentElement.closest('div[dir="auto"]') !== el) continue;
        const t = textOf(el);
        if (t.length < MIN_SIGNAL_CHARS || seen.has(t) || t === authorName || /^[\d.,\s]*[KkMm]?$/.test(t)) continue;
        seen.add(t);
        addMsg(stripControls(t));
      }
    }
    const images = extractImageText(post);
    if (images) msgs.push(images);
    return stripControls(S.normaliseText(msgs.join('\n'))).slice(0, MAX_TEXT);
  }

  // Facebook's own "See more" / "See translation" controls end up inside the text block.
  const FB_CONTROLS = /(?:…|\.\.\.)?\s*\b(?:See more|See less|See translation|See original|Rate this translation)\b/gi;
  function stripControls(t) {
    return t.replace(FB_CONTROLS, ' ')
      .replace(/(?:\s+·)+(?=\s|$)/g, ' ')  // "· See original · Rate this translation" leaves bare dots
      .replace(/\s+/g, ' ').trim();
  }

  /** Facebook's automatic image descriptions ("May be an image of text that says '…'").
   *  They often carry the scam text of image-only posts. Profile pictures are skipped. */
  function extractImageText(post) {
    const out = [];
    for (const img of post.querySelectorAll('img[alt]')) {
      if (insideNestedArticle(img, post) || img.closest('h2, h3, h4, strong, [data-ad-rendering-role="profile_name"]')) continue;
      const alt = S.normaliseText(img.getAttribute('alt'));
      if (alt.length < 12 || /^no photo description/i.test(alt) || out.includes(alt)) continue;
      out.push(alt);
      if (out.length >= 4) break;
    }
    return out.length ? `Image: ${out.join(' | ')}` : '';
  }

  /** The feed post that contains `node`, if any (for content that loads after the post shell). */
  function closestPost(node) {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    if (!el || !el.closest) return null;
    const wrapper = el.closest('[aria-posinset], [data-pagelet^="FeedUnit"]');
    if (wrapper) return wrapper;
    let article = el.closest('[role="article"]');
    // Climb out of comment articles to the post's own article.
    while (article && article.parentElement && article.parentElement.closest('[role="article"]')) {
      article = article.parentElement.closest('[role="article"]');
    }
    return article && !isComment(article) ? article : null;
  }

  /** Permalink without Facebook's tracking parameters (__cft__, __tn__, ...). */
  function cleanPermalink(u) {
    const q = new URLSearchParams();
    for (const [k, v] of u.searchParams) if (!k.startsWith('__') && k !== 'ref' && k !== 's') q.set(k, v);
    const s = q.toString();
    return `https://www.facebook.com${u.pathname}${s ? `?${s}` : ''}`;
  }

  function extractLinks(post) {
    const urls = [];
    let permalink = null, postId = null, best = PERMALINK_PATTERNS.length;
    for (const a of post.querySelectorAll('a[href]')) {
      if (insideNestedArticle(a, post)) continue;
      const href = a.getAttribute('href');
      const abs = S.safeUrl(href);
      if (!abs) continue;
      if (S.isExternalLink(abs.href)) {
        const u = S.unwrapFacebookLink(abs.href);
        if (u && !urls.includes(u)) urls.push(u);
        continue;
      }
      // Prefer the post's own link (/posts/, story_fbid) over links to its photos or videos,
      // which the timestamp link only reveals after FBShieldReveal() has hovered it.
      if (best > 0 && S.isFacebookHost(abs.hostname)) {
        for (let i = 0; i < best; i++) {
          const m = (abs.pathname + abs.search).match(PERMALINK_PATTERNS[i]);
          if (m) { postId = m[1]; permalink = cleanPermalink(abs); best = i; break; }
        }
      }
    }
    return { urls: urls.slice(0, 20), permalink, postId };
  }

  function isSponsored(post) {
    // FB scrambles the visible "Sponsored" label; the ad-specific attributes are a better hint.
    return !!post.querySelector('a[href*="/ads/about"], [aria-label="Sponsored"]');
  }

  /** Extract the minimum signals needed to score one post.
   *  Returns null while the post is still an empty loading shell; returns {empty: true}
   *  for a loaded post with nothing to analyse (e.g. a video with no caption). */
  function extractPost(post) {
    const author = extractAuthor(post);
    if (author.name && SELECTORS.nonPostAuthors.test(author.name.trim())) {
      return { empty: true, fingerprint: S.fingerprint(`unit|${author.name.toLowerCase()}`) };
    }
    const text = extractText(post, author.name);
    const { urls, permalink, postId } = extractLinks(post);
    if (!text && !urls.length) {
      return author.name ? { empty: true, fingerprint: S.fingerprint(`empty|${author.name.toLowerCase()}|${postId || ''}`) } : null;
    }
    const fp = S.fingerprint(`${text.toLowerCase()}|${urls.join(' ')}|${(author.name || '').toLowerCase()}`);
    return {
      empty: false,
      fingerprint: fp,
      post: {
        post_id: postId,
        permalink,
        text,
        urls,
        author_name: author.name,
        author_url: author.url,
        is_sponsored: isSponsored(post),
      },
    };
  }

  const api = { SELECTORS, findPosts, closestPost, extractPost, extractText, extractLinks, extractAuthor,
                cleanProfileUrl, stripControls };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FBShieldExtractor = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
