# Liyu Kids Mart Image Cache Worker

This Worker serves Telegram-hosted product images through Cloudflare's edge cache while keeping the existing Supabase `file_id` records unchanged.

## How it works

- Requests arrive at `/media/<file_id>`.
- The Worker resolves `file_id` with Telegram's `getFile` API.
- It fetches the image from Telegram.
- Cloudflare caches the response globally.

## Required secret

Set `TELEGRAM_BOT_TOKEN` as a Worker secret.

## Public URL

Use the Worker subdomain URL:

- `https://liyu-kids-mart.liyukidsmart.workers.dev/media/<file_id>`

## Notes

- Keep the existing Vercel app running.
- Keep storing Telegram `file_id` values in Supabase.
- Do not migrate the 155 image records.
