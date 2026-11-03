/**
 * Compatibility for legacy Prisma databases in the 0.17.x upstream runtime.
 *
 * Upstream runs legacy SQL in db.transaction(). PRAGMA foreign_keys=OFF
 * inside such a transaction is a SQLite no-op. DROP/rename of Note then
 * fires ON DELETE SET NULL and destroys pId relationships. Disable foreign
 * keys OUTSIDE each legacy transaction, restore them, and check integrity.
 *
 * This only patches the known migration implementation in the disposable
 * container filesystem. It does not replace startup or copy/overwrite a DB.
 * An unknown upstream implementation fails validation instead of guessing.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { pathToFileURL } from 'node:url';

export const MARKER = 'LAZYCAT_LEGACY_FOREIGN_KEY_FIX_V1';
const LEGACY_LOOP = `for (const file of pendingLegacyFiles) {
\t\t\t\tconst sql = await readSql(file.path);
\t\t\t\tdb.transaction(() => {
\t\t\t\t\tdb.exec(sql);
\t\t\t\t})();
\t\t\t\tapplied.push(file.id);
\t\t\t}`;

export function patchMigration(source) {
  if (source.includes(MARKER)) return source;
  if (source.split(LEGACY_LOOP).length !== 2) {
    throw new Error('Unknown upstream legacy migrator; adapter compatibility review is required.');
  }
  return source.replace(LEGACY_LOOP, `for (const file of pendingLegacyFiles) {
                const sql = await readSql(file.path);
                // ${MARKER}: PRAGMA must be outside db.transaction().
                db.pragma("foreign_keys = OFF");
                try {
                    db.transaction(() => { db.exec(sql); })();
                } finally {
                    db.pragma("foreign_keys = ON");
                }
                applied.push(file.id);
            }
            if (db.prepare("PRAGMA foreign_key_check").all().length) {
                throw new Error("Legacy migration left invalid foreign keys");
            }`);
}

export function archiveLegacyTasks(databaseUrl) {
  if (!databaseUrl?.startsWith('file:')) throw new Error('Expected an explicit SQLite file URL.');
  const path = databaseUrl.slice(5);
  if (!existsSync(path)) return;
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    if (!db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='Task'").get()) return;
    const tasks = db.prepare('SELECT * FROM Task ORDER BY id').all();
    if (!tasks.length) return;
    // Upstream removes the old Task feature; preserve every field in a
    // readable archive as well as the upstream full pre-migration DB backup.
    const bytes = JSON.stringify({ format: 'i-notebook-legacy-tasks-v1', tasks },
      (_key, value) => typeof value === 'bigint' ? value.toString() : value, 2) + '\n';
    const hash = createHash('sha256').update(bytes).digest('hex').slice(0, 16);
    const directory = join(dirname(path), 'legacy-task-exports');
    mkdirSync(directory, { recursive: true });
    const target = join(directory, `tasks-${hash}.json`);
    if (!existsSync(target)) writeFileSync(target, bytes, { flag: 'wx', mode: 0o600 });
    if (readFileSync(target, 'utf8') !== bytes) throw new Error('Legacy task archive verification failed.');
    console.log(`Archived ${tasks.length} legacy task(s) to ${target}; the new UI uses collections.`);
  } finally {
    db.close();
  }
}

export function prepareRuntime(directory = '/app/server/_ssr') {
  const candidates = readdirSync(directory).filter(name => name.endsWith('.mjs'))
    .map(name => join(directory, name))
    .filter(path => {
      const source = readFileSync(path, 'utf8');
      return source.includes('getAppliedPrismaMigrations') && source.includes('pendingLegacyFiles');
    });
  if (candidates.length !== 1) throw new Error('Cannot identify exactly one upstream legacy migration module.');
  const path = candidates[0];
  const original = readFileSync(path, 'utf8');
  const patched = patchMigration(original);
  if (patched !== original) writeFileSync(path, patched);
  archiveLegacyTasks(process.env.DATABASE_URL);
  console.log('Legacy database compatibility preparation completed.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  prepareRuntime();
}
