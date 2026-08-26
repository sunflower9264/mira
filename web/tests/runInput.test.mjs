import assert from 'node:assert/strict';
import test from 'node:test';

import { hasRunInputContent } from '../src/lib/runInput.ts';

test('run input accepts text, files, or both but rejects an empty submission', () => {
  assert.equal(hasRunInputContent('生成一组纪念冰箱贴', 0), true);
  assert.equal(hasRunInputContent('', 2), true);
  assert.equal(hasRunInputContent('保留人物表情', 2), true);
  assert.equal(hasRunInputContent('   ', 0), false);
});
