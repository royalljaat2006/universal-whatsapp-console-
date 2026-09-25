# Media architecture (Build Cycle 2)

Media bytes never go into PostgreSQL. The database holds metadata
(`MediaAsset`, `MessageAttachment`). The bytes live in object storage behind
Django's `storages["media"]` alias.

## Storage

| Item | Value |
|---|---|
| Backend | `STORAGES["media"]`. Dev: `FileSystemStorage` at `MEDIA_ROOT`. Production: any Django storage (for example an S3-compatible backend), a settings change only |
| Wrapper | `apps/media/storage.py` (`save`, `open_file`, `iter_chunks`, `read_bytes`, `delete`, with `MediaStorageError` on failure) |
| Key layout | `org/<org_id>/biz/<business_id>/YYYY/MM/<uuid7hex><ext>`. Keys never contain user filenames |
| Checksums | sha256 and size are computed while writing and stored on `MediaAsset` |

Keys are namespaced by tenant and business. Every read still goes through an
org-scoped, business-restricted query first (`MediaContentView`), so the key
itself grants nothing.

## Outbound (agent → customer)

```
POST /api/v1/media/ (multipart, messages.send, X-Business-ID required)
  → validate_media(): allowed type + extension + size + magic bytes (apps/media/rules.py)
  → store in object storage → MediaAsset(source=upload)
POST /api/v1/messages/ {type: "media", media: {media_asset_id, caption?}}
  → same business as the conversation? window open? caption allowed for kind?
  → Message(queued) + MessageAttachment(role=body, media_asset)
dispatch (outbound queue)
  → provider media id cached < 29 days ? reuse : POST /{PHONE_NUMBER_ID}/media
  → POST /{PHONE_NUMBER_ID}/messages {type: <kind>, <kind>: {id, caption?}}
```

- The upload to Meta is **retry-safe** (a failed upload never sends a message),
  so every non-permanent upload error is retried. The message send keeps the
  usual rule that ambiguous POST failures are never retried.
- Template media headers use the same path: the header asset is uploaded at
  dispatch, and `{"asset_id"}` is replaced by the provider media id.

## Inbound (customer → business)

```
webhook message with image/video/audio/document/sticker
  → MessageAttachment(storage_status=pending, provider_media_id, mime, sha256)
  → on commit: download_inbound_media (media queue)
       GET /{MEDIA_ID}?phone_number_id=…   (fresh 5-minute URL every attempt)
       GET <url>  (https + host allow-list + streamed size cap)
       validate kind rule → store → MediaAsset(source=inbound) → attachment stored
       → realtime message.media_ready
  → retries with backoff up to MAX_DOWNLOAD_ATTEMPTS (6); then storage_status=failed + last_error
  → sweep_pending_media_downloads (beat, every 10 min) re-queues stragglers
```

`last_error` holds `<CODE>: <reason>`.

| Outcome | Codes |
|---|---|
| Retried | `CONFIGURATION_REQUIRED` (no usable credential yet), `MEDIA_STORAGE_FAILED`, `MEDIA_URL_EXPIRED` (a new URL is fetched next attempt), retryable provider errors |
| Failed at once | `MEDIA_UNAVAILABLE` (media id unknown or expired), `UNSUPPORTED_MEDIA_TYPE`, `MEDIA_TOO_LARGE`, `MEDIA_URL_REJECTED` (non-https or non-allow-listed host), `MEDIA_DOWNLOAD_FAILED`, `MEDIA_ID_MISSING` |

## Serving

`GET /api/v1/media/<id>/content/` streams the bytes after the organization
and business checks, with:

- `Content-Security-Policy: sandbox; default-src 'none'` and
  `X-Content-Type-Options: nosniff`, so user-supplied files can never run as
  an active document on our origin;
- `inline` disposition for image, audio, video and sticker, and `attachment`
  for everything else;
- `Cache-Control: private, max-age=300`.

The browser cannot put `X-Organization-ID` on an `<img src>`, so the console
fetches media with the tenant headers (`apiBlob`) and shows it through an
object URL. Images load automatically. Audio, video and documents load when
the user asks.

## Limits

nginx allows 101 MB bodies on `/api/v1/media/` only (100 MB is the WhatsApp
document maximum). Everything else keeps the default limit. See
[api-assumptions.md](integrations/meta/api-assumptions.md#media) for the
per-type maximums.
