import { Link } from 'react-router';
import { useI18n } from '../i18n';
import type { InspectionSummary } from '../lib/inspections';
import { StatusBadge } from './StatusBadge';

export interface InspectionActions {
  onOpen: (inspection: InspectionSummary) => void;
  onProgress: (inspection: InspectionSummary) => void;
  onRetry: (inspection: InspectionSummary) => void;
}

export function InspectionRow({
  inspection,
  onOpen,
  onProgress,
  onRetry,
}: InspectionActions & { inspection: InspectionSummary }) {
  const { t, lang } = useI18n();
  const date = new Date(inspection.date);
  const locale = lang === 'en' ? 'en-GB' : lang;
  const day = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(date);
  const time = new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
  const action =
    inspection.status === 'done'
      ? { label: t('list.openMap'), callback: onOpen }
      : inspection.status === 'failed'
        ? { label: t('list.retry'), callback: onRetry }
        : { label: t('list.showProgress'), callback: onProgress };

  return (
    <div className="qv-row inspections-list-row" role="row" data-inspection-id={inspection.id}>
      <span className="qv-row__name" role="cell">
        {inspection.siteName || t('list.unnamedObject')}
      </span>
      <span role="cell">
        <time dateTime={inspection.date}>{`${day}, ${time}`}</time>
      </span>
      <span role="cell">
        <StatusBadge status={inspection.status} />
      </span>
      <span className="qv-row__num inspections-list-count" role="cell">
        {inspection.findingsCount === null
          ? t('list.noCount')
          : new Intl.NumberFormat(locale).format(inspection.findingsCount)}
      </span>
      <span className="inspections-list-action" role="cell">
        {inspection.status === 'done' && (
          <Link
            className="qv-btn qv-btn--quiet"
            to={`/inspections/${encodeURIComponent(inspection.id)}/report`}
          >
            {t('report.link')}
          </Link>
        )}
        <button
          type="button"
          className="qv-btn qv-btn--secondary"
          onClick={() => action.callback(inspection)}
        >
          {action.label}
        </button>
      </span>
    </div>
  );
}
