import { createZvecGrep } from '@zvec/zvec-grep';
import { createInterface } from 'node:readline';
import { writeFileSync, renameSync } from 'node:fs';
import { resolve, join } from 'node:path';

const root = resolve(process.argv[2]);
const model = process.argv[3] || 'local/potion-multilingual-128m';
if (!model.startsWith('local/')) throw new Error('Only local embedding models are permitted.');
const engine = await createZvecGrep({
  root, embedding: model, device: 'cpu', embeddingConcurrency: 4,
  modelCacheDir: process.env.LITDB_MODEL_CACHE || resolve(root, '..', 'models'),
});
let lastProgress = 0;
function progress(value) {
  if (Date.now() - lastProgress < 1500 && value.phase !== 'done') return;
  lastProgress = Date.now();
  const path = resolve(root, '..', 'index_progress.json');
  const data = { ...value, at: new Date().toISOString(), pid: process.pid };
  writeFileSync(path + '.tmp', JSON.stringify(data));
  renameSync(path + '.tmp', path);
  process.stderr.write(JSON.stringify(data) + '\n');
}
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
try {
  for await (const line of lines) {
    let request;
    try {
      request = JSON.parse(line);
      let result;
      if (request.op === 'index') {
        result = await engine.index({
          // A copied index may still name its previous corpus directory.
          rootPaths: [root],
          includePaths: ['papers'], globs: ['**/*.txt'],
          rebuild: Boolean(request.rebuild), embeddingConcurrency: 4, onProgress: progress,
        });
      } else if (request.op === 'status') {
        result = await engine.info({ includeStatus: request.full !== false });
      } else if (request.op === 'query') {
        result = await engine.context({
          query: request.routes?.length ? undefined : request.query, routes: request.routes,
          fuse: true, autoUpdate: false, limit: request.limit || 30,
          includePaths: Array.isArray(request.include_paths) ? request.include_paths : undefined, trace: true,
        });
      } else if (request.op === 'close') {
        break;
      } else {
        throw new Error('Unknown search operation');
      }
      process.stdout.write('LITDB_JSON ' + JSON.stringify({ id: request.id, ok: true, result }) + '\n');
    } catch (error) {
      process.stdout.write('LITDB_JSON ' + JSON.stringify({
        id: request?.id, ok: false, error: error?.message || String(error),
        code: error?.code, cause: error?.cause?.message,
      }) + '\n');
    }
  }
} finally {
  await engine.close();
}
