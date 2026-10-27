import { useEffect, useState, type ReactEventHandler } from 'react';
import { useI18n } from '../i18n';
import { authHeader } from './auth';

/** An img cannot send a Bearer header; fetch protected frames into a short-lived blob URL. */
export function SessionImage({
  src,
  alt,
  className,
  onLoad,
  onError,
}: {
  src: string;
  alt: string;
  className: string;
  onLoad?: ReactEventHandler<HTMLImageElement>;
  onError?: ReactEventHandler<HTMLImageElement>;
}) {
  const { t } = useI18n();
  const authorization = src.startsWith('/api/runs/') ? authHeader().Authorization : undefined;
  const [image, setImage] = useState<{ src: string; url?: string; failed?: boolean } | null>(null);

  useEffect(() => {
    if (!authorization) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    void (async () => {
      try {
        const response = await fetch(src, {
          headers: { Authorization: authorization },
          signal: controller.signal,
          cache: 'no-store',
        });
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({ src, url: objectUrl });
      } catch {
        if (!controller.signal.aborted) setImage({ src, failed: true });
      }
    })();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src, authorization]);

  if (!authorization)
    return <img src={src} alt={alt} className={className} onLoad={onLoad} onError={onError} />;
  if (image?.src === src && image.failed)
    return (
      <p role="alert" className="p-3 text-sm text-watch">
        {t('auth.frameError')}
      </p>
    );
  return (
    <img
      src={image?.src === src ? image.url : undefined}
      alt={alt}
      className={className}
      onLoad={onLoad}
      onError={onError}
    />
  );
}
