import { useEffect, useRef, useState, type RefObject } from 'react';
import type { Bundle } from '../lib/bundle';
import { UNASSIGNED_SITE_ID } from '../lib/inspections';
import { deleteRoi, loadRoi, resolveRoiSiteId, saveRoi } from '../lib/roi';
import type { Key } from '../i18n';
import type { ViewerEngine } from './engine';
import type { Roi, RoiBox } from './roi';

/** All durable state comes from the catalog API; browser storage is never a fallback. */
export function useRoi(
  bundle: Bundle,
  knownSiteId: string | undefined,
  userId: string | null,
  engine: RefObject<ViewerEngine | null>,
  initialHideOutside = false,
) {
  const [box, setBox] = useState<RoiBox | null>(null);
  const [saved, setSaved] = useState<Roi | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [rect, setRect] = useState<{ x: number; y: number; width: number; height: number } | null>(
    null,
  );
  const [hidden, setHidden] = useState(false);
  const [open, setOpen] = useState(bundle.runId !== 'demo');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<Key | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const demo = bundle.runId === 'demo';
  // Direct development viewer routes already permit the API's tokenless dev mode.
  const author = userId ?? (import.meta.env.DEV ? 'development' : null);

  useEffect(() => {
    const current = ++generation.current;
    const abort = new AbortController();
    setBox(null);
    setSaved(null);
    setSiteId(null);
    setHidden(false);
    setDirty(false);
    setError(null);
    setBusy(!demo);
    if (!demo) {
      void (async () => {
        try {
          const site =
            knownSiteId && knownSiteId !== UNASSIGNED_SITE_ID
              ? knownSiteId
              : await resolveRoiSiteId(bundle.runId, abort.signal);
          const roi = await loadRoi(site, abort.signal);
          if (abort.signal.aborted || current !== generation.current) return;
          setSiteId(site);
          setBox(roi);
          setSaved(roi);
          setHidden(roi !== null && initialHideOutside);
          if (roi && roi.source_run !== bundle.runId) setOpen(true);
        } catch {
          if (abort.signal.aborted || current !== generation.current) return;
          setError('roi.loadError');
          setOpen(true);
        } finally {
          if (!abort.signal.aborted && current === generation.current) setBusy(false);
        }
      })();
    }
    return () => {
      abort.abort();
      generation.current = current + 1;
    };
  }, [bundle, knownSiteId, userId, demo, revision, initialHideOutside]);

  useEffect(() => engine.current?.setRoi(box), [box, bundle, engine]);
  useEffect(() => engine.current?.setHideOutsideRoi(hidden), [hidden, bundle, engine]);

  const drawn = (next: RoiBox) => {
    setBox(next);
    setSaved(null);
    setDirty(true);
    setError(null);
    setOpen(true);
  };

  const persist = async () => {
    if (!box || !siteId || !author || busy || drawing) return;
    const current = generation.current;
    setBusy(true);
    setError(null);
    try {
      const roi = await saveRoi(siteId, {
        version: 1,
        site_id: siteId,
        source_run: bundle.runId,
        frame: 'reconstruction',
        up_axis: 'z',
        min: box.min,
        max: box.max,
        created_at: new Date().toISOString(),
        created_by: author,
      });
      if (current !== generation.current) return;
      setBox(roi);
      setSaved(roi);
      setDirty(false);
    } catch {
      if (current === generation.current) setError('roi.saveError');
    } finally {
      if (current === generation.current) setBusy(false);
    }
  };

  const clear = async () => {
    if (!box || busy || drawing) return;
    const current = generation.current;
    setBusy(true);
    setError(null);
    try {
      // Delete even a replacement draft: the site may still have an earlier saved box.
      if (!demo) {
        if (!siteId) throw new Error('Site is not resolved');
        await deleteRoi(siteId);
      }
      if (current !== generation.current) return;
      setBox(null);
      setSaved(null);
      setDirty(false);
      setHidden(false);
    } catch {
      if (current === generation.current) setError('roi.clearError');
    } finally {
      if (current === generation.current) setBusy(false);
    }
  };

  return {
    box,
    saved,
    drawing,
    setDrawing,
    rect,
    setRect,
    hidden,
    setHidden,
    open,
    setOpen,
    busy,
    dirty,
    error,
    demo,
    canSave: !!box && !!siteId && !!author && !demo && !busy && !drawing && dirty,
    drawn,
    drawError: () => setError('roi.drawError'),
    persist,
    clear,
    retry: () => setRevision((value) => value + 1),
  };
}
