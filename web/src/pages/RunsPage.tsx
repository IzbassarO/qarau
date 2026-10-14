import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { useI18n } from '../i18n';
import type { Source, Stats } from '../lib/bundle';
import { LangSwitch, Logo } from '../viewer/ui';

interface RunSummary {
  run_id: string;
  source: Source;
  stats: Stats;
}

export function RunsPage() {
  const { t } = useI18n();
  const q = useQuery({
    queryKey: ['runs'],
    queryFn: async () => {
      const r = await fetch('/api/runs');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as RunSummary[];
    },
  });
  return (
    <div className="min-h-full bg-bg">
      <header className="flex items-center gap-4 border-b border-line bg-surface px-4 py-2.5">
        <Logo />
        <div className="grow" />
        <LangSwitch />
      </header>
      <main className="mx-auto max-w-3xl p-6">
        <h1 className="mb-4 text-2xl font-semibold leading-8">{t('runs.title')}</h1>
        {q.isError && (
          <p className="text-watch">
            {t('error.generic')}: {String(q.error)}
          </p>
        )}
        {q.data?.length === 0 && <p className="text-ink-2">{t('runs.empty')}</p>}
        {!!q.data?.length && (
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr className="text-left text-xs uppercase tracking-[0.06em] text-ink-2">
                <th className="py-2 font-medium">{t('runs.col.name')}</th>
                <th className="font-medium">{t('runs.col.kind')}</th>
                <th className="text-right font-medium">{t('runs.col.frames')}</th>
                <th className="text-right font-medium">{t('runs.col.points')}</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((r) => (
                <tr key={r.run_id} className="border-t border-line">
                  <td className="py-2.5">
                    <Link className="text-link hover:underline" to={`/viewer/${r.run_id}`}>
                      {r.run_id}
                    </Link>
                  </td>
                  <td className="text-ink-2">
                    {t(r.source.kind === 'images' ? 'kind.images' : 'kind.video')}
                  </td>
                  <td className="text-right font-mono">{r.stats.frames_extracted}</td>
                  <td className="text-right font-mono">{r.stats.points.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </div>
  );
}
