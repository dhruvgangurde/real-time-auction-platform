import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

// Project rules from the design brief (section 13) and OR-11. Each entry names the item it
// enforces; src/lint.test.ts proves every one of them fires.
const CATALOGUE = 'Strings come from the catalogue (OR-11): useStrings(), never a literal in JSX.';
const TEXT_ATTRS = '/^(alt|title|placeholder|aria-label|aria-description|aria-valuetext)$/';

export const restrictedSyntax = [
  {
    // item 24: user text is rendered as text
    selector: 'JSXAttribute[name.name="dangerouslySetInnerHTML"]',
    message: 'Render user text as text (brief 13 item 24).',
  },
  // OR-11: no string literal reaches the screen except through the catalogue
  { selector: 'JSXText[value=/\\S/]', message: CATALOGUE },
  { selector: 'JSXExpressionContainer > Literal[raw=/^[\'"]/][value=/\\S/]', message: CATALOGUE },
  { selector: 'JSXExpressionContainer > TemplateLiteral', message: CATALOGUE },
  { selector: `JSXAttribute[name.name=${TEXT_ATTRS}] > Literal`, message: CATALOGUE },
  {
    selector: `JSXAttribute[name.name=${TEXT_ATTRS}] > JSXExpressionContainer > :matches(Literal, TemplateLiteral)`,
    message: CATALOGUE,
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
