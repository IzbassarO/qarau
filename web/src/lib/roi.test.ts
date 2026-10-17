import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Roi } from '../viewer/roi';
import { UNASSIGNED_SITE_ID } from './inspections';
import { deleteRoi, loadRoi, resolveRoiSiteId, roiSiteId, saveRoi } from './roi';

const roi: Roi = {
  version: 1,
  site_id: 'north:tank',
  source_run: 'survey-1',
  frame: 'reconstruction',
  up_axis: 'z',
  min: [-2, -3, -1],
  max: [2, 3, 4],
  created_at: '2026-10-17T12:00:00Z',
  created_by: 'operator',
};

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe('ROI API', () => {
  it('loads the saved site region with authentication and an abort signal', async () => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-roi-credential');
    const fetch = vi.fn().mockResolvedValue(Response.json(roi));
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;

    await expect(loadRoi(roi.site_id, signal)).resolves.toEqual(roi);
    expect(fetch).toHaveBeenCalledWith('/api/sites/north%3Atank/roi', {
      signal,
      headers: { Authorization: 'Bearer test-roi-credential' },
    });
  });

  it('returns null only when no region has been saved', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    await expect(loadRoi(roi.site_id)).resolves.toBeNull();
  });

  it.each([401, 403, 500, 503])('surfaces a load failure with HTTP %s', async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status })));
    await expect(loadRoi(roi.site_id)).rejects.toThrow(`HTTP ${status}`);
  });

  it('validates server responses and refuses a region for another site', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(Response.json({ ...roi, max: [0, -4, 1] }))
        .mockResolvedValueOnce(Response.json({ ...roi, site_id: 'another-site' })),
    );
    await expect(loadRoi(roi.site_id)).rejects.toThrow();
    await expect(loadRoi(roi.site_id)).rejects.toThrow('site_id');
  });

  it('saves JSON on the server and returns the validated saved region', async () => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-roi-credential');
    const fetch = vi.fn().mockResolvedValue(Response.json(roi));
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;

    await expect(saveRoi(roi.site_id, roi, signal)).resolves.toEqual(roi);
    expect(fetch).toHaveBeenCalledWith('/api/sites/north%3Atank/roi', {
      method: 'PUT',
      signal,
      headers: {
        Authorization: 'Bearer test-roi-credential',
        'Content-Type': 'application/json',
      },
      body: expect.any(String),
    });
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(roi);
  });

  it('does not send an invalid region or a mismatched site to the server', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    await expect(saveRoi(roi.site_id, { ...roi, min: [NaN, 0, 0] })).rejects.toThrow();
    await expect(saveRoi('other-site', roi)).rejects.toThrow('site_id');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('surfaces save failures and malformed saved responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(new Response(null, { status: 503 }))
        .mockResolvedValueOnce(Response.json({})),
    );
    await expect(saveRoi(roi.site_id, roi)).rejects.toThrow('HTTP 503');
    await expect(saveRoi(roi.site_id, roi)).rejects.toThrow();
  });

  it('deletes the server region with the session header', async () => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-roi-credential');
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetch);
    await expect(deleteRoi(roi.site_id)).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith('/api/sites/north%3Atank/roi', {
      method: 'DELETE',
      signal: undefined,
      headers: { Authorization: 'Bearer test-roi-credential' },
    });
  });

  it('surfaces server and network failures instead of clearing silently', async () => {
    const networkError = new Error('Connection lost');
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(new Response(null, { status: 500 }))
        .mockRejectedValueOnce(networkError)
        .mockRejectedValueOnce(networkError),
    );
    await expect(deleteRoi(roi.site_id)).rejects.toThrow('HTTP 500');
    await expect(loadRoi(roi.site_id)).rejects.toBe(networkError);
    await expect(saveRoi(roi.site_id, roi)).rejects.toBe(networkError);
  });
});

describe('ROI site identity', () => {
  it('uses a real catalog site and gives nested site data precedence', () => {
    expect(
      roiSiteId('survey-1', [
        { run_id: 'survey-1', site: { id: 'north' }, site_id: 'legacy-site' },
      ]),
    ).toBe('north');
    expect(roiSiteId('survey-1', [{ run_id: 'survey-1', site_id: 'north' }])).toBe('north');
  });

  it('uses the route run id for missing or unassigned sites, never another run or manifest id', () => {
    expect(roiSiteId('notre-dame', [])).toBe('notre-dame');
    expect(roiSiteId('notre-dame', [{ run_id: 'notre-dame' }])).toBe('notre-dame');
    expect(roiSiteId('notre-dame', [{ run_id: 'notre-dame', site_id: UNASSIGNED_SITE_ID }])).toBe(
      'notre-dame',
    );
    expect(roiSiteId('notre-dame', [{ run_id: 'other-run', site_id: 'north' }])).toBe('notre-dame');
  });

  it('resolves against the authenticated catalog and propagates catalog failures', async () => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-roi-credential');
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json([{ run_id: 'survey-1', site_id: 'north' }]))
      .mockResolvedValueOnce(new Response(null, { status: 503 }));
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;
    await expect(resolveRoiSiteId('survey-1', signal)).resolves.toBe('north');
    expect(fetch).toHaveBeenCalledWith('/api/runs', {
      signal,
      headers: { Authorization: 'Bearer test-roi-credential' },
    });
    await expect(resolveRoiSiteId('survey-1')).rejects.toThrow('503');
  });
});
