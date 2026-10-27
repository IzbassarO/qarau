import { useI18n } from '../i18n';
import { sortAndCap, type InspectionSummary } from '../lib/inspections';
import { InspectionRow, type InspectionActions } from './InspectionRow';
import './inspections-list.css';

export function InspectionsList({
  inspections,
  ...actions
}: InspectionActions & { inspections: readonly InspectionSummary[] }) {
  const { t } = useI18n();
  const { rows, total } = sortAndCap(inspections);

  if (total === 0)
    return (
      <div className="qv-list">
        <div className="qv-empty">
          <b>{t('list.emptyTitle')}</b>
          <p>{t('list.emptyDescription')}</p>
        </div>
      </div>
    );

  return (
    <div className="inspections-list">
      <div className="qv-list" role="table" aria-label={t('list.title')}>
        <div className="qv-row qv-row--head inspections-list-row" role="row">
          <span role="columnheader">{t('list.object')}</span>
          <span role="columnheader">{t('list.date')}</span>
          <span role="columnheader">{t('list.status')}</span>
          <span className="inspections-list-count" role="columnheader">
            {t('list.findings')}
          </span>
          <span role="columnheader">{t('list.action')}</span>
        </div>
        {rows.map((inspection) => (
          <InspectionRow key={inspection.id} inspection={inspection} {...actions} />
        ))}
      </div>
      {total > rows.length && (
        <p className="qv-caption inspections-list-total">
          {t('list.count')
            .replace('{shown}', String(rows.length))
            .replace('{total}', String(total))}
        </p>
      )}
    </div>
  );
}
