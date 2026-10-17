import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, test } from 'node:test';
import { prepareRoiFixture } from './prepare-roi.mjs';

let root;
let source;
let out;
let manifest;
let original;
const createdAt = '2026-10-17T12:00:00Z';

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'qarau-a7-fixture-test-'));
  source = path.join(root, 'source');
  out = path.join(root, 'review-catalog');
  const run = path.join(source, 'notre-dame');
  fs.mkdirSync(path.join(run, 'web/thumbs'), { recursive: true });
  const positions = [];
  for (let y = 0; y <= 10; y++) for (let x = 0; x <= 10; x++) positions.push(x, y, x + y);
  const bytes = Buffer.alloc(positions.length * 4);
  positions.forEach((value, i) => bytes.writeFloatLE(value, i * 4));
  const files = {
    'web/points.position.f32': bytes,
    'web/points.first_seen.u16': Buffer.alloc(121 * 2),
    'web/points.num_obs.u8': Buffer.alloc(121),
    'web/thumbs/000001.png': Buffer.from('test thumbnail bytes'),
    'capture.mp4': Buffer.from('do not copy source captures'),
  };
  for (const [rel, contents] of Object.entries(files))
    fs.writeFileSync(path.join(run, rel), contents);
  manifest = {
    version: 1,
    run_id: 'notre-dame',
    world: { up: [0, 0, 1] },
    points: {
      count: 121,
      position: 'web/points.position.f32',
      first_seen: 'web/points.first_seen.u16',
      num_obs: 'web/points.num_obs.u8',
    },
    frames: [{ thumb: 'web/thumbs/000001.png' }],
  };
  fs.writeFileSync(path.join(run, 'web/manifest.json'), JSON.stringify(manifest));
  original = new Map(
    [...Object.keys(files), 'web/manifest.json'].map((rel) => [
      rel,
      fs.readFileSync(path.join(run, rel)),
    ]),
  );
});

afterEach(() => fs.rmSync(root, { recursive: true, force: true }));

test('prepares an explicit saved fixture with pure ROI math, copied viewer assets and an unchanged source', () => {
  const result = prepareRoiFixture({ source, out, createdAt });
  assert.equal(result.catalog, out);
  assert.equal(result.route, '/viewer/notre-dame');
  const roi = JSON.parse(fs.readFileSync(path.join(out, 'sites/notre-dame/roi.json'), 'utf8'));
  assert.deepEqual(roi, {
    version: 1,
    site_id: 'notre-dame',
    source_run: 'notre-dame',
    frame: 'reconstruction',
    up_axis: 'z',
    min: [0, 0, -1],
    max: [10, 10, 21],
    created_at: createdAt,
    created_by: 'a7-visual-test-fixture',
  });
  assert.match(result.metadata.purpose, /not an engineer decision/);
  assert.equal(result.metadata.copied_assets, 4);
  for (const [rel, bytes] of original) {
    assert.deepEqual(fs.readFileSync(path.join(source, 'notre-dame', rel)), bytes);
    if (rel !== 'capture.mp4')
      assert.deepEqual(fs.readFileSync(path.join(out, 'notre-dame', rel)), bytes);
  }
  assert.equal(fs.existsSync(path.join(out, 'notre-dame/capture.mp4')), false);
  assert.equal(
    fs.lstatSync(path.join(out, 'notre-dame/web/points.position.f32')).isSymbolicLink(),
    false,
  );
});

test('retains a source site association instead of inventing a separate site', () => {
  manifest.site = { id: 'north-site', name: 'North' };
  fs.writeFileSync(path.join(source, 'notre-dame/web/manifest.json'), JSON.stringify(manifest));
  const result = prepareRoiFixture({ source, out, createdAt });
  assert.equal(result.roi.site_id, 'north-site');
  assert.equal(fs.existsSync(path.join(out, 'sites/north-site/roi.json')), true);
});

test('never overwrites an existing directory or writes inside the source catalog', () => {
  fs.mkdirSync(out);
  fs.writeFileSync(path.join(out, 'keep'), 'existing review');
  assert.throws(() => prepareRoiFixture({ source, out, createdAt }), /new directory/);
  assert.equal(fs.readFileSync(path.join(out, 'keep'), 'utf8'), 'existing review');
  assert.throws(
    () => prepareRoiFixture({ source, out: path.join(source, 'output'), createdAt }),
    /must not overlap/,
  );
  assert.equal(fs.existsSync(path.join(source, 'output')), false);
});

test('rejects output under data, including a symlink redirect into data', () => {
  const data = path.join(root, 'data');
  fs.mkdirSync(data);
  assert.throws(
    () => prepareRoiFixture({ source, out: path.join(data, 'review'), createdAt }),
    /never under data/,
  );
  const link = path.join(root, 'redirect');
  fs.symlinkSync(data, link);
  assert.throws(
    () => prepareRoiFixture({ source, out: path.join(link, 'review'), createdAt }),
    /never under data/,
  );
  assert.deepEqual(fs.readdirSync(data), []);
});

test('rejects malformed buffers, traversal assets and unsafe sites before creating output', () => {
  manifest.points.count++;
  fs.writeFileSync(path.join(source, 'notre-dame/web/manifest.json'), JSON.stringify(manifest));
  assert.throws(() => prepareRoiFixture({ source, out, createdAt }), /declared XYZ/);
  assert.equal(fs.existsSync(out), false);
  manifest.points.count--;
  manifest.points.position = '../notre-dame/web/points.position.f32';
  fs.writeFileSync(path.join(source, 'notre-dame/web/manifest.json'), JSON.stringify(manifest));
  // A normalised path resolving to an actual file in the same run is safe.
  prepareRoiFixture({ source, out, createdAt });
  fs.rmSync(out, { recursive: true });
  manifest.points.position = '../../outside';
  fs.writeFileSync(path.join(root, 'outside'), 'outside source run');
  fs.writeFileSync(path.join(source, 'notre-dame/web/manifest.json'), JSON.stringify(manifest));
  assert.throws(() => prepareRoiFixture({ source, out, createdAt }), /escapes/);
  assert.equal(fs.existsSync(out), false);
  manifest.points.position = 'web/points.position.f32';
  manifest.site_id = '../outside';
  fs.writeFileSync(path.join(source, 'notre-dame/web/manifest.json'), JSON.stringify(manifest));
  assert.throws(() => prepareRoiFixture({ source, out, createdAt }), /site_id/);
  assert.equal(fs.existsSync(out), false);
});
