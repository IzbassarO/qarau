// Explicit visual-test preparation, never imported by the app or run on startup.
// Usage: pnpm roi:prepare --source /read-only/runs --out /scratch/review-catalog
// Copies only viewer assets; customer captures and the source catalog are untouched.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { boxFromPointsInRect, serialize } from '../src/viewer/roi.ts';

const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const isInside = (parent, child) => child === parent || child.startsWith(parent + path.sep);
const quantile = (sorted, fraction) => sorted[Math.floor((sorted.length - 1) * fraction)];

/** Build an isolated catalog with an explicitly labelled, sample-derived test ROI. */
export function prepareRoiFixture({ source, out, run = 'notre-dame', createdAt }) {
  if (!source || !out) throw new Error('Both --source and --out are required.');
  if (!RUN_ID.test(run)) throw new Error('The run id must be a single safe catalog name.');
  const sourceRoot = fs.realpathSync(source);
  const requestedOut = path.resolve(out);
  if (fs.existsSync(requestedOut) || fs.lstatSync(requestedOut, { throwIfNoEntry: false }))
    throw new Error('The output must be a new directory; existing catalogs are never replaced.');
  // Resolve the parent before creating anything so symlinks cannot redirect output.
  const outputRoot = path.join(fs.realpathSync(path.dirname(requestedOut)), path.basename(out));
  if (requestedOut.split(path.sep).includes('data') || outputRoot.split(path.sep).includes('data'))
    throw new Error('Test output must be in scratch, never under data/.');
  if (isInside(sourceRoot, outputRoot) || isInside(outputRoot, sourceRoot))
    throw new Error('The source and output catalogs must not overlap.');
  const runRoot = fs.realpathSync(path.join(sourceRoot, run));
  if (!isInside(sourceRoot, runRoot)) throw new Error('The source run escapes its catalog.');
  const manifestPath = path.join(runRoot, 'web/manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (
    ![1, 2].includes(manifest.version) ||
    !Array.isArray(manifest.world?.up) ||
    manifest.world.up.length !== 3 ||
    manifest.world.up.some((value, axis) => value !== (axis === 2 ? 1 : 0))
  )
    throw new Error('The fixture requires a version 1 or 2, Z-up viewer bundle.');
  const siteId = manifest.site?.id || manifest.site_id;
  const site = siteId && siteId !== 'qarau:unassigned' ? siteId : run;
  const assets = new Map();
  const addAsset = (rel) => {
    if (typeof rel !== 'string' || !rel.trim() || path.isAbsolute(rel))
      throw new Error('A viewer asset must have a relative path.');
    const normalized = path.normalize(rel);
    const lexical = path.resolve(runRoot, normalized);
    const absolute = fs.realpathSync(lexical);
    if (
      !isInside(runRoot, lexical) ||
      !isInside(runRoot, absolute) ||
      !fs.statSync(absolute).isFile() ||
      normalized === path.join('web', 'manifest.json')
    )
      throw new Error('A viewer asset escapes the source run or replaces its manifest.');
    assets.set(normalized, absolute);
    return absolute;
  };
  const pointsPath = addAsset(manifest.points?.position);
  for (const key of ['first_seen', 'num_obs']) addAsset(manifest.points?.[key]);
  for (const key of ['color', 'source', 'confidence'])
    if (manifest.points[key]) addAsset(manifest.points[key]);
  for (const value of Object.values(manifest.tracks ?? {})) addAsset(value);
  if (manifest.far) {
    addAsset(manifest.far.position);
    addAsset(manifest.far.color);
  }
  for (const frame of manifest.frames ?? []) addAsset(frame.thumb);
  if (manifest.findings) {
    const findingsPath = addAsset(manifest.findings);
    const findings = JSON.parse(fs.readFileSync(findingsPath, 'utf8'));
    addAsset(findings.point_finding);
  }
  const bytes = fs.readFileSync(pointsPath);
  if (
    !Number.isSafeInteger(manifest.points.count) ||
    manifest.points.count <= 0 ||
    bytes.length !== manifest.points.count * 12
  )
    throw new Error('The object position buffer must contain the declared XYZ float32 triplets.');
  // Explicit little-endian decoding matches the viewer bundle format, independently
  // of Buffer allocation alignment and the host's byte order.
  const positions = new Float32Array(bytes.length / 4);
  for (let i = 0; i < positions.length; i++) positions[i] = bytes.readFloatLE(i * 4);
  const xs = [];
  const ys = [];
  for (let i = 0; i < positions.length; i += 3) {
    if (![positions[i], positions[i + 1], positions[i + 2]].every(Number.isFinite)) continue;
    xs.push(positions[i]);
    ys.push(positions[i + 1]);
  }
  if (!xs.length) throw new Error('The object cloud has no finite points.');
  xs.sort((a, b) => a - b);
  ys.sort((a, b) => a - b);
  const footprint = {
    min: [quantile(xs, 0.02), quantile(ys, 0.02), 0],
    max: [quantile(xs, 0.98), quantile(ys, 0.98), 0],
  };
  const box = boxFromPointsInRect(positions, footprint, 'z');
  if (!box) throw new Error('The finite object cloud has no nonzero rectangular footprint.');
  const roi = {
    version: 1,
    site_id: site,
    source_run: run,
    frame: 'reconstruction',
    up_axis: 'z',
    ...box,
    created_at: createdAt ?? new Date().toISOString(),
    created_by: 'a7-visual-test-fixture',
  };
  const serialized = serialize(roi);
  let inside = 0;
  for (let i = 0; i < positions.length; i += 3)
    if (
      [0, 1, 2].every(
        (axis) => positions[i + axis] >= box.min[axis] && positions[i + axis] <= box.max[axis],
      )
    )
      inside++;
  const metadata = {
    purpose: 'A7 visual test fixture; generated automatically, not an engineer decision.',
    run_id: run,
    site_id: site,
    selection: 'Finite object-cloud XY 2nd–98th percentiles; selected full Z plus 5% per side.',
    points_inside: inside,
    points_outside: manifest.points.count - inside,
    copied_assets: assets.size,
  };
  fs.mkdirSync(outputRoot);
  try {
    const outputRun = path.join(outputRoot, run);
    fs.mkdirSync(path.join(outputRun, 'web'), { recursive: true });
    fs.copyFileSync(manifestPath, path.join(outputRun, 'web/manifest.json'));
    for (const [rel, absolute] of assets) {
      const destination = path.join(outputRun, rel);
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      fs.copyFileSync(absolute, destination, fs.constants.COPYFILE_FICLONE);
    }
    const siteDir = path.join(outputRoot, 'sites', site);
    fs.mkdirSync(siteDir, { recursive: true });
    fs.writeFileSync(path.join(siteDir, 'roi.json'), serialized + '\n', { flag: 'wx' });
    fs.writeFileSync(
      path.join(outputRoot, 'roi-test-fixture.json'),
      JSON.stringify(metadata, null, 2) + '\n',
      { flag: 'wx' },
    );
  } catch (error) {
    // Only a fresh directory created by this invocation is removed on failure.
    fs.rmSync(outputRoot, { recursive: true, force: true });
    throw error;
  }
  return { catalog: outputRoot, route: `/viewer/${encodeURIComponent(run)}`, roi, metadata };
}

function main() {
  const args = process.argv.slice(2);
  const options = {};
  for (let i = 0; i < args.length; i += 2) {
    const name = args[i];
    if (!['--source', '--out', '--run'].includes(name) || !args[i + 1])
      throw new Error(
        'Usage: pnpm roi:prepare --source <catalog> --out <new-scratch-catalog> [--run <id>]',
      );
    options[name.slice(2)] = args[i + 1];
  }
  const result = prepareRoiFixture(options);
  console.log(JSON.stringify(result, null, 2));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
