import assert from 'node:assert/strict';
import { collectFilteredHits, FILTER_BATCH_SIZE } from './filter-batches.mjs';

const ids = Array.from({length: 25003}, (_, n) => String(n));
for (const descending of [true, false]) {
  const seen = [];
  const result = collectFilteredHits({fileIds: [...ids, ids[0]], groupIds:['group']}, 7, batch => {
    assert.ok(batch.fileIds.length <= FILTER_BATCH_SIZE);
    assert.deepEqual(batch.groupIds, ['group']);
    seen.push(...batch.fileIds);
    return batch.fileIds.map(id => ({fragment:{id},score:Number(id)}))
      .sort((a,b) => descending ? b.score-a.score : a.score-b.score).slice(0,7);
  }, descending);
  assert.deepEqual(seen, ids);
  const expected = descending ? ids.slice(-7).reverse() : ids.slice(0,7);
  assert.deepEqual(result.map(hit=>hit.fragment.id), expected);
}
console.log('PASS: bounded IN batches preserve global raw-score order in both routes.');
