# Frontend

React + TypeScript (strict) with Vite. The design brief (`docs/design-brief.md`) is the spec; the room's client rules are its section 13 and `docs/architecture.md` section 4.

## Commands

```
nvm use                 # Node 24.19 (.nvmrc); engines refuse other majors
npm ci                  # exact versions from the lockfile
npm run dev             # Vite on 5173, /api and /media proxied to the compose stack (8011)
npm run build           # tsc --noEmit, then vite build into dist/ (served by the web service on 8011)
npm test                # Vitest: reducer, money, clock, strings, contract, client, socket, lint rules
npm run lint            # ESLint, including the project rules below
npm run e2e             # Playwright against http://localhost:8011 only (the real CSP), after npm run build
```

Playwright never runs against the dev server: the content security policy only exists on 8011.

## Dependencies

Exact versions only (`.npmrc` has `save-exact`). A new version is taken only once it is at least seven days old. Everything in `package.json` is from brief 11.2; nothing else without a one-line reason in `docs/decisions.md`.

## Project lint rules

| Rule | Brief |
|---|---|
| `no-console` | 13 item 19: a maximum never reaches the console |
| `dangerouslySetInnerHTML` banned | 13 item 24: user text is rendered as text |

`src/lint.test.ts` runs ESLint on small samples and proves each rule fires.
