import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import { archiveLegacyTasks, patchMigration } from '../content/prepare-runtime.mjs';

const loop = `for (const file of pendingLegacyFiles) {
\t\t\t\tconst sql = await readSql(file.path);
\t\t\t\tdb.transaction(() => {
\t\t\t\t\tdb.exec(sql);
\t\t\t\t})();
\t\t\t\tapplied.push(file.id);
\t\t\t}`;

async function runMigration(source) {
  const native = new DatabaseSync(':memory:');
  native.exec(`PRAGMA foreign_keys=ON;
    CREATE TABLE Note(id TEXT PRIMARY KEY, pId TEXT REFERENCES Note(id) ON DELETE SET NULL);
    INSERT INTO Note VALUES ('parent',NULL),('child','parent');`);
  const sql = `PRAGMA foreign_keys=OFF;
    CREATE TABLE new_Note(id TEXT PRIMARY KEY, pId TEXT REFERENCES Note(id) ON DELETE SET NULL);
    INSERT INTO new_Note SELECT * FROM Note;
    DROP TABLE Note; ALTER TABLE new_Note RENAME TO Note; PRAGMA foreign_keys=ON;`;
  const db = {
    pragma: value => native.exec('PRAGMA ' + value),
    exec: value => native.exec(value),
    prepare: value => native.prepare(value),
    transaction: callback => () => {
      native.exec('BEGIN');
      try { callback(); native.exec('COMMIT'); }
      catch (error) { native.exec('ROLLBACK'); throw error; }
    },
  };
  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    await new AsyncFunction('db', 'pendingLegacyFiles', 'readSql', 'applied', source)(
      db, [{ id: 'test', path: 'fixture.sql' }], async () => sql, []);
    return native.prepare("SELECT pId FROM Note WHERE id='child'").get().pId;
  } finally { native.close(); }
}

test('real SQLite reproduction loses hierarchy; patched transaction preserves it', async () => {
  assert.equal(await runMigration(loop), null);
  assert.equal(await runMigration(patchMigration(loop)), 'parent');
});

test('runtime fix is idempotent and unknown migrators fail closed', () => {
  const patched = patchMigration(loop);
  assert.equal(patchMigration(patched), patched);
  assert.throws(() => patchMigration('unknown migration'), /compatibility review/);
});

test('all legacy task fields survive in a persistent, idempotent archive', () => {
  const directory = mkdtempSync(join(tmpdir(), 'notebook-tasks-'));
  const path = join(directory, 'db.sqlite');
  const db = new DatabaseSync(path);
  try {
    db.exec("CREATE TABLE Task(id TEXT, title TEXT, progress INTEGER); INSERT INTO Task VALUES ('old-id','旧任务',73)");
    archiveLegacyTasks('file:' + path);
    archiveLegacyTasks('file:' + path);
    const files = readdirSync(join(directory, 'legacy-task-exports'));
    assert.equal(files.length, 1);
    const data = JSON.parse(readFileSync(join(directory, 'legacy-task-exports', files[0]), 'utf8'));
    assert.equal(data.tasks[0].title, '旧任务');
    assert.equal(data.tasks[0].progress, 73);
    db.exec('DROP TABLE Task');
    archiveLegacyTasks('file:' + path);
    assert.deepEqual(readdirSync(join(directory, 'legacy-task-exports')), files);
  } finally {
    db.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
