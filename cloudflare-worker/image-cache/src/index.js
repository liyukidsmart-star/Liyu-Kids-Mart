/**
 * Liyu Kids Mart — Image Cache Worker
 *
 * Serves Telegram-stored product images via Cloudflare's edge cache.
 * On cache miss: fetch the file path from Telegram API, then stream
 * the image bytes to the client and cache at the edge for 1 year.
 *
 * Cached images are served instantly on subsequent requests (no Telegram
 * round-trip needed).
 */

const CDN_CACHE_TTL = 31_536_000; // 1 year in seconds
const STALE_TTL = 604_800;        // 7 days stale-while-revalidate
const FILE_INFO_TTL = 3_600;      // 1 hour for Telegram getFile response

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}

function cacheHeaders(sourceHeaders, fileId) {
  const headers = new Headers();

  // Copy safe headers from upstream
  const SAFE = ['content-type', 'content-length', 'etag', 'last-modified'];
  for (const h of SAFE) {
    const v = sourceHeaders.get(h);
    if (v) headers.set(h, v);
  }

  // Force long-lived immutable caching — Telegram file IDs are stable
  headers.set('cache-control', `public, max-age=${CDN_CACHE_TTL}, immutable`);
  headers.set('cdn-cache-control', `public, max-age=${CDN_CACHE_TTL}, stale-while-revalidate=${STALE_TTL}`);
  headers.set('vary', 'Accept');
  headers.set('x-image-source', 'telegram');
  headers.set('x-file-id', fileId.substring(0, 20) + '...');
  headers.set('access-control-allow-origin', '*');
  headers.set('cross-origin-resource-policy', 'cross-origin');

  return headers;
}

export default {
  async fetch(request, env, ctx) {
    // Only allow GET and HEAD
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method not allowed', { status: 405 });
    }

    const url = new URL(request.url);

    // Health check
    if (url.pathname === '/health') {
      return jsonResponse({ ok: true, service: 'liyu-image-cache' });
    }

    if (!url.pathname.startsWith('/media/')) {
      return new Response('Not found', { status: 404 });
    }

    const fileId = decodeURIComponent(url.pathname.slice('/media/'.length)).trim();
    if (!fileId) {
      return new Response('Missing file_id', { status: 400 });
    }

    const token = (env.TELEGRAM_BOT_TOKEN || '').trim();
    if (!token) {
      return new Response('Telegram token not configured', { status: 503 });
    }

    // ── Check Cloudflare Cache ──────────────────────────────────────────
    const cache = caches.default;
    // Use a canonical cache key (strip query strings)
    const cacheKey = new Request(
      `${url.origin}/media/${encodeURIComponent(fileId)}`,
      { method: 'GET', headers: { accept: request.headers.get('accept') || 'image/*' } }
    );

    const cached = await cache.match(cacheKey);
    if (cached) {
      // Serve from edge cache — fastest path
      const res = new Response(cached.body, cached);
      res.headers.set('x-cache', 'HIT');
      return res;
    }

    // ── Step 1: Resolve Telegram file path ─────────────────────────────
    let fileInfoData;
    try {
      const fileInfoResp = await fetch(
        `https://api.telegram.org/bot${token}/getFile?file_id=${encodeURIComponent(fileId)}`,
        {
          cf: {
            cacheTtl: FILE_INFO_TTL,  // Cache the getFile result for 1 hour
            cacheEverything: true,
          },
        }
      );
      fileInfoData = await fileInfoResp.json();
    } catch (err) {
      return new Response('Telegram file lookup failed', { status: 502 });
    }

    if (!fileInfoData?.ok || !fileInfoData?.result?.file_path) {
      const msg = fileInfoData?.description || 'File not found on Telegram';
      return jsonResponse({ ok: false, error: msg }, 404);
    }

    const filePath = fileInfoData.result.file_path;

    // ── Step 2: Fetch the actual image bytes ────────────────────────────
    let upstreamResp;
    try {
      upstreamResp = await fetch(
        `https://api.telegram.org/file/bot${token}/${filePath}`,
        {
          cf: {
            cacheEverything: true,
            cacheTtl: CDN_CACHE_TTL,
          },
        }
      );
    } catch (err) {
      return new Response(`Telegram image fetch failed: ${err.message}`, {
        status: 502,
        headers: { 'cache-control': 'no-store' },
      });
    }

    if (!upstreamResp.ok) {
      return new Response(`Telegram fetch failed: ${upstreamResp.status}`, {
        status: 502,
        headers: { 'cache-control': 'no-store' },
      });
    }

    // ── Step 3: Build cached response ───────────────────────────────────
    const headers = cacheHeaders(upstreamResp.headers, fileId);
    const response = new Response(upstreamResp.body, {
      status: 200,
      headers,
    });

    // Store in edge cache asynchronously (don't block the response)
    ctx.waitUntil(cache.put(cacheKey, response.clone()));

    const res = response.clone();
    res.headers.set('x-cache', 'MISS');
    return res;
  },
};
