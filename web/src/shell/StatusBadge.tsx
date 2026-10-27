import { useI18n } from '../i18n';
import type { InspectionSummary } from '../lib/inspections';

export function StatusBadge({ status }: { status: InspectionSummary['status'] }) {
  const { t } = useI18n();
  return <span className="qv-status qv-status--muted">{t(`list.status.${status}`)}</span>;
}
