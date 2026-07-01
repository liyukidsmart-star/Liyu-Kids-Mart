function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}

function cacheHeaders(sourceHeaders) {
  const headers = new Headers(sourceHeaders);
  headers.set('cache-control', 'public, max-age=31536000, s-maxage=31536000, stale-while-revalidate=604800');
  headers.set('cdn-cache-control', 'public, max-age=31536000, stale-while-revalidate=604800');
  headers.set('vary', 'Accept');
  headers.set('x-image-source', 'telegram');
  headers.delete('set-cookie');
  return headers;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method not allowed', { status: 405 });
    }

    const url = new URL(request.url);
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

    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: 'GET' });
    const cached = await cache.match(cacheKey);
    if (cached) {
      return cached;
    }

    const fileInfoResp = await fetch(
      `https://api.telegram.org/bot${token}/getFile?file_id=${encodeURIComponent(fileId)}`,
      { cf: { cacheTtl: 0, cacheEverything: false } },
    );

    let fileInfo;
    try {
      fileInfo = await fileInfoResp.json();
    } catch (err) {
      return new Response('Telegram file lookup failed', { status: 502 });
    }

    if (!fileInfoResp.ok || !fileInfo.ok || !fileInfo.result || !fileInfo.result.file_path) {
      const message = (fileInfo && fileInfo.description) ? fileInfo.description : 'File not found';
      return jsonResponse({ ok: false, error: message }, 404);
    }

    const filePath = fileInfo.result.file_path;
    const upstreamResp = await fetch(
      `https://api.telegram.org/file/bot${token}/${filePath}`,
      { cf: { cacheEverything: true } },
    );

    if (!upstreamResp.ok) {
      return new Response(`Telegram fetch failed: ${upstreamResp.status}`, {
        status: 502,
        headers: { 'cache-control': 'no-store' },
      });
    }

    const headers = cacheHeaders(upstreamResp.headers);
    const response = new Response(upstreamResp.body, {
      status: upstreamResp.status,
      headers,
    });

    ctx.waitUntil(cache.put(cacheKey, response.clone()));
    return response;
  },
};
