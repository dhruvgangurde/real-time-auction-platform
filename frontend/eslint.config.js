import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

// Project rules from the design brief (section 13) and OR-11. Each entry names the item it
// enforces; src/lint.test.ts proves every one of them fires.
export const restrictedSyntax = [
  {
    // item 24: user text is rendered as text
    selector: 'JSXAttribute[name.name="dangerouslySetInnerHTML"]',
    message: 'Render user text as text (brief 13 item 24).',
  },
];

export default defineConfig([
  globalIgnores(['dist/', 'node_modules/', 'playwright-report/', 'test-results/']),
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // item 19: nothing a viewer owns (a maximum) ever reaches the console
      'no-console': 'error',
      'no-restricted-syntax': ['error', ...restrictedSyntax],
    },
  },
]);
