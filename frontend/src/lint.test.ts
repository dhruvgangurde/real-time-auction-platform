// The project lint rules must fire (brief 13 items 19, 20, 24 and OR-11). Each sample is linted
// with the real eslint.config.js through ESLint's API; a rule that stops firing fails here.
import { ESLint } from 'eslint';
import { describe, expect, test } from 'vitest';

const eslint = new ESLint();

async function messages(code: string, file = 'src/sample.tsx'): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: file });
  return (result?.messages ?? []).map((m) => `${m.ruleId ?? 'parse'}: ${m.message}`);
}

const OK = `declare const s: { alt: string; title: string };
export const a = <img alt={s.alt} />;
export const b = <p title={s.title}>{s.title}{' '}{3}</p>;
`;

describe('strings only come from the catalogue (OR-11)', () => {
  test('a catalogue-driven component passes', async () => {
    expect(await messages(OK)).toEqual([]);
  });

  test('text between tags fails', async () => {
    expect(await messages('export const a = <p>Hello</p>;')).toContainEqual(
      expect.stringMatching(/no-restricted-syntax.*catalogue/),
    );
  });

  test('a string literal in braces fails', async () => {
    expect(await messages("export const a = <p>{'Hello'}</p>;")).toContainEqual(
      expect.stringMatching(/no-restricted-syntax.*catalogue/),
    );
    expect(await messages('export const a = <p>{`Hello`}</p>;')).toContainEqual(
      expect.stringMatching(/no-restricted-syntax.*catalogue/),
    );
  });

  test('a literal in a text-bearing attribute fails', async () => {
    for (const attr of ['alt', 'title', 'placeholder', 'aria-label']) {
      expect(await messages(`export const a = <img ${attr}="A camera" />;`), attr).toContainEqual(
        expect.stringMatching(/no-restricted-syntax.*catalogue/),
      );
      expect(await messages(`export const a = <img ${attr}={'A camera'} />;`), attr).toContainEqual(
        expect.stringMatching(/no-restricted-syntax.*catalogue/),
      );
    }
  });
});

describe('privacy and safety rules', () => {
  test('console is banned (item 19)', async () => {
    expect(await messages('console.log(1);', 'src/sample.ts')).toContainEqual(
      expect.stringMatching(/^no-console:/),
    );
  });

  test('dangerouslySetInnerHTML is banned (item 24)', async () => {
    expect(
      await messages('declare const h: string; export const a = <div dangerouslySetInnerHTML={{ __html: h }} />;'),
    ).toContainEqual(expect.stringMatching(/no-restricted-syntax.*as text/));
  });
});
