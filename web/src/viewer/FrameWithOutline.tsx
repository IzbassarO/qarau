import { useEffect, useRef, useState } from 'react';
import { SessionImage } from '../auth/SessionImage';
import { useI18n } from '../i18n';
import type { EvidenceFrame, Severity } from '../lib/findings';
import { containFit, polygonPoints } from '../lib/outline';

export function FrameWithOutline({
  evidence,
  severity,
  dimensions,
  label,
}: {
  evidence: EvidenceFrame;
  severity: Severity;
  dimensions?: { width: number; height: number };
  label: string;
}) {
  const { t } = useI18n();
  const boxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 400, height: 250 });
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const size = natural ?? dimensions;

  useEffect(() => {
    const element = boxRef.current;
    if (!element) return;
    const measure = () => {
      const { width, height } = element.getBoundingClientRect();
      if (width > 0 && height > 0) setBox({ width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={boxRef} className="qv-thumb__img evidence-frame">
      <SessionImage
        src={evidence.image_url}
        alt={label}
        className="evidence-image"
        onLoad={(event) => {
          const { naturalWidth: width, naturalHeight: height } = event.currentTarget;
          if (width > 0 && height > 0) setNatural({ width, height });
        }}
        onError={() => setFailed(true)}
      />
      {failed ? (
        <p role="alert" className="qv-caption evidence-image-error">
          {t('auth.frameError')}
        </p>
      ) : (
        size && (
          <svg
            className="evidence-outline"
            viewBox={`0 0 ${box.width} ${box.height}`}
            aria-hidden="true"
          >
            <polygon
              className={`qv-mask${severity === 'watch' ? ' qv-mask--watch' : ''}`}
              points={polygonPoints(
                evidence.outline,
                containFit(size.width, size.height, box.width, box.height),
              )}
            />
          </svg>
        )
      )}
    </div>
  );
}
