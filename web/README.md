# WikINT — Frontend

Next.js 16 / React 19 frontend for the WikINT platform.

For full project documentation see the [root README](../README.md) and [docs/](../docs/).

## Development

```bash
pnpm install
pnpm dev                     # http://localhost:3000
```

Configuration comes from the root `.env` file (via `NEXT_PUBLIC_*` variables). There is no separate `web/.env` file. API routing in development is handled by the dev Nginx config in `infra/`.

## Commands

```bash
pnpm test              # vitest unit tests
pnpm lint              # eslint
pnpm tsc --noEmit      # type-check
pnpm i18n:check        # verify all translation keys are present
pnpm generate-api-types  # regenerate src/lib/api-types.ts (API must be running)
pnpm knip              # dead-code detection
```
