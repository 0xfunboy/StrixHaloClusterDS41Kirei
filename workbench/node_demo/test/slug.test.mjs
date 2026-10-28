import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSlug } from '../dist/slug.js';

test('normalizes spaces, punctuation and accents', () => {
  assert.equal(normalizeSlug('  Hello, WORLD!  '), 'hello-world');
  assert.equal(normalizeSlug('Crème brûlée'), 'creme-brulee');
  assert.equal(normalizeSlug('___'), 'item');
});
