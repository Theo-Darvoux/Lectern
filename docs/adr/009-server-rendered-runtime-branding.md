# ADR 009 — Server-rendered runtime branding

## Status

Accepted

## Context

Branding (site name, colors, favicon, wordmark, link-preview metadata) is set
with environment variables that the API reads. The web app was a static export
served by nginx, so its HTML was fixed when the image was built:

- Released images are built without branding, so every page first showed the
  default name, color and favicon. The real branding appeared only after the
  JavaScript had loaded and fetched `GET /api/auth/methods`.
- Some settings were read with `process.env` at build time only (favicon, theme
  color, Open Graph tags). They never changed in a released image.
- Link-preview crawlers do not run JavaScript. Nginx detected them by
  User-Agent and sent them to an HTML stub rendered by the API (`/api/og`).
  Search engines were left out of this list, so they indexed the default title.
- The banner endpoint (`/api/og/image`) drew any title passed in the query
  string, so anyone could create a banner with arbitrary text on the domain.
- Dynamic routes needed workarounds: `dynamicParams = false` with a `_` shell
  page, plus nginx rewrites that mapped every real URL to that shell.
- The web container received the full `.env`, secrets included, although it
  used none of it.

## Decision

1. The web app runs the Next.js server (`output: "standalone"`, `node server.js`)
   instead of a static export.
2. The root layout reads `GET /api/auth/methods` and `GET /api/og/meta?path=/`
   from the API on every request (`API_INTERNAL_URL`, 3 s timeout), and renders:
   - the title, description, favicon, theme color and Open Graph / Twitter tags
     (`generateMetadata`, `generateViewport`);
   - the brand color as CSS variables on `<html>` and the wordmark font
     stylesheet in `<head>`;
   - the public config into a per-request config store, so server HTML and the
     first client render show the same values.
3. Resource pages (`/browse/…`, `/profile/<id>`, `/qcm/<id>`) add their own
   metadata from `GET /api/og/meta?path=…`. The API resolves the path as an
   anonymous visitor, so private resources fall back to the site metadata.
4. `GET /api/og/image` takes only `path`, resolves the text itself, and is rate
   limited. URLs in the metadata are built from `FRONTEND_URL`, not from the
   `Host` header.
5. The User-Agent routing in nginx, the `/api/og` HTML stub, the `_` shell pages
   and the nginx shell rewrites are removed. Components that must keep their
   state across ids (browser, profile, PR preview) are layouts placed above the
   dynamic segment.
6. The web container gets no `env_file`. The image holds only build facts
   (`NEXT_PUBLIC_COMMIT_SHA`, `NEXT_PUBLIC_API_URL`).

## Consequences

- No flash of the default branding. Browsers, search engines and link-preview
  crawlers all receive the same HTML. A rebrand needs an API restart only.
- One image serves every deployment.
- Production runs a Node process (512 MB limit) instead of a 64 MB nginx.
  Every page is rendered per request, with two or three internal API calls.
- If the API is down while a page renders, the page uses neutral defaults
  and the browser fetches the config itself, as before.
- The API is the only source of branding defaults and fallbacks.
