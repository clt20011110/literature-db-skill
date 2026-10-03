// Keep native IN lists below zvec's 20,000-element limit. Merge raw route
// scores before zvec-grep computes global ranks and reciprocal-rank fusion.
export const FILTER_BATCH_SIZE = 10000;

export function collectFilteredHits(filter, limit, search, descending) {
  const ids = [...new Set(filter.fileIds)];
  const hits = new Map();
  const compare = (a, b) => (descending ? b.score - a.score : a.score - b.score)
    || a.fragment.id.localeCompare(b.fragment.id);
  for (let offset = 0; offset < ids.length; offset += FILTER_BATCH_SIZE) {
    const batch = { ...filter, fileIds: ids.slice(offset, offset + FILTER_BATCH_SIZE) };
    for (const hit of search(batch)) {
      const previous = hits.get(hit.fragment.id);
      if (!previous || compare(hit, previous) < 0) hits.set(hit.fragment.id, hit);
    }
  }
  return [...hits.values()].sort(compare).slice(0, limit);
}
