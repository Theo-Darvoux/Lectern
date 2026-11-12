# ADR 008 — Card previews embedded in listings

## Status

Accepted

## Context

Each material card asked `GET /api/materials/{id}/thumbnail` for a signed URL, then
loaded the image. The browser ran at most 4 of these lookups at a time, so a
directory of 50 materials filled in slowly, card by card.

Text and code files were rendered to an image by LibreOffice. This cost 1 to 3 s of
CPU per file, and the result was a hard-to-read page of small text.

The worker and the API also classified files separately (extension lists in the
renderer, other lists in the router), so a type could get a broken fallback, such as
a DjVu file shown in an `<img>`.

## Decision

1. Listings that render cards (directory listings, home page, profiles, saved items,
   material details and attachments) add `thumbnail_url` and `thumbnail_type` to
   each `current_version_info`:
   - `"webp"`: a generated thumbnail.
   - `"fallback"`: no thumbnail, so the original image, video or PDF, which the
     browser renders itself.
   - `"none"`: the file has no preview; the client shows a generic card and makes
     no request.
   - absent (`null`): the listing did not resolve it, so the client asks the
     thumbnail endpoint. Search results and link targets work this way.
2. `api/app/core/media/thumbnails.py` is the one place that maps a MIME type to a
   renderer and decides which types have a native fallback. The worker and the API
   both use it.
3. Text, code, Markdown and notebook files get no thumbnail. The card shows the
   language or format (`web/src/lib/code-languages.ts`).
4. A video card shows its WebP thumbnail and requests the video file only when the
   pointer first enters the card, then plays it.

## Consequences

- One request per listing instead of one per card. A listing response costs one
  cached signature per material (a Redis read, or a local HMAC or S3 signature).
- Signed URLs expire (15 minutes for presigned S3 URLs). A card whose image fails
  to load requests a freshly signed URL once before retrying.
- After a regeneration, the embedded URL is stale; the card then asks the endpoint.
- Existing text thumbnails were cleared by the `drop_text_thumbnails` migration.
  Their objects are removed by the storage Integrity Check on the staff System page.
