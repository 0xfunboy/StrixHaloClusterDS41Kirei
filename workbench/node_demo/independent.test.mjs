import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const modulePath = pathToFileURL('/work/dist/slug.js').href;
const { normalizeSlug } = await import(modulePath);

test('independent Unicode and separator cases', () => {
  assert.equal(normalizeSlug(' déjà--VU '), 'deja-vu');
  assert.equal(normalizeSlug('A😀B'), 'a-b');
  assert.equal(normalizeSlug('---alpha---'), 'alpha');
  assert.equal(normalizeSlug('💥'), 'item');
});
