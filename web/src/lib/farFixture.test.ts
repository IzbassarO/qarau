import { afterEach, expect, it, vi } from 'vitest';
import { makeFarBundle } from '../../e2e/fixtures/far-layer/makeFarBundle';
import { FarLayerError, loadBundle, loadManifest, runUrl } from './bundle';

afterEach(() => vi.unstubAllGlobals());

async function parseFixture({ manifest, files }: ReturnType<typeof makeFarBundle>) {
  const runId = manifest.run_id;
  const prefix = runUrl(runId, '');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === `/api/runs/${encodeURIComponent(runId)}/manifest.json`)
        return Response.json(manifest);
      const body = url.startsWith(prefix) ? files.get(url.slice(prefix.length)) : undefined;
      return body === undefined
        ? new Response(null, { status: 404 })
        : new Response(Uint8Array.from(body).buffer);
    }),
  );
  return loadBundle(runId, await loadManifest(runId), vi.fn());
}

it('parses the synthetic e2e fixture with all object and far points', async () => {
  const fixture = makeFarBundle();
  const bundle = await parseFixture(fixture);
  expect(bundle.manifest.points.count).toBe(300);
  expect(bundle.manifest.far?.count).toBe(200);
  expect(bundle.position).toHaveLength(fixture.manifest.points.count * 3);
  expect(bundle.color).toHaveLength(fixture.manifest.points.count * 3);
  expect(bundle.firstSeen).toHaveLength(fixture.manifest.points.count);
  expect(bundle.assembled).toEqual(new Uint32Array([fixture.manifest.points.count]));
  expect(bundle.far?.position).toHaveLength(fixture.manifest.far.count * 3);
  expect(bundle.far?.color).toHaveLength(fixture.manifest.far.count * 3);
  expect(bundle.farError).toBeUndefined();
});

it('warns for the broken e2e fixture while preserving all object points', async () => {
  const intact = await parseFixture(makeFarBundle());
  const error: unknown = await parseFixture(makeFarBundle({ brokenFar: true })).catch(
    (error: unknown) => error,
  );
  expect(error).toBeInstanceOf(FarLayerError);
  const failure = error as FarLayerError;
  expect(failure.reason).toBe('broken');
  expect(failure.message).toContain('XYZ/RGB triplets');
  expect(failure.bundle.farError).toBe('broken');
  expect(failure.bundle.far).toBeUndefined();
  expect(failure.bundle.position).toHaveLength(intact.manifest.points.count * 3);
  expect(failure.bundle.position).toEqual(intact.position);
  expect(failure.bundle.color).toEqual(intact.color);
  expect(failure.bundle.firstSeen).toEqual(intact.firstSeen);
  expect(failure.bundle.assembled).toEqual(intact.assembled);
});
