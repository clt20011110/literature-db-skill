// Compatibility patch for pinned zvec-grep 0.2.2: its flat OR filter crashes
// native parsing for large scopes. Native IN has a 20,000-value limit, so
// search each route in bounded batches and merge its raw scores before RRF.
// https://zvec.org/en/docs/db/data-operations/query/filter/
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const pkg = resolve(root, 'node_modules/@zvec/zvec-grep');
if (JSON.parse(readFileSync(resolve(pkg, 'package.json'))).version !== '0.2.2') {
  throw new Error('Review the LitDB filter patch before changing zvec-grep versions.');
}
const target = resolve(pkg, 'dist/engine/storage/zvec.js');
const marker = '// litdb-bounded-native-filters-v1';
let source = readFileSync(target, 'utf8');
if (source.includes(marker)) {
  console.log('LitDB native-filter patch already applied.');
} else {
  function replaceOnce(before, after) {
    if (source.split(before).length !== 2) throw new Error('Unexpected zvec-grep source; filter patch not applied.');
    source = source.replace(before, after);
  }
  const helper = relative(dirname(target), resolve(root, 'filter-batches.mjs')).split('\\').join('/');
  source = `${marker}\nimport { collectFilteredHits, FILTER_BATCH_SIZE } from ${JSON.stringify(helper)};\n` + source;
  for (const [method, parameter, descending] of [
    ['searchFts', 'query', true], ['searchVector', 'vector', false],
  ]) {
    const signature = `    ${method}(${parameter}, limit, filter) {\n`;
    replaceOnce(signature, signature +
      `        if (filter?.fileIds?.length > FILTER_BATCH_SIZE) {\n` +
      `            return collectFilteredHits(filter, limit, subset => this.${method}(${parameter}, limit, subset), ${descending});\n` +
      `        }\n`);
  }
  replaceOnce(
    '    return `(${values.map((value) => `${field} = ${quoteFilterString(value)}`).join(" OR ")})`;',
    '    return `${field} IN (${values.map(quoteFilterString).join(", ")})`;');
  writeFileSync(target, source);
  console.log('Applied LitDB bounded native-filter patch to zvec-grep 0.2.2.');
}
