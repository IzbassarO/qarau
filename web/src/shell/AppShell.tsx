import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useI18n } from '../i18n';
import { createDemo } from '../fixtures/demo';
import { useInspections } from '../lib/inspections';
import { Sidebar } from './Sidebar';
import { InspectionView } from './InspectionView';
import { NewInspectionDialog } from './NewInspectionDialog';
import './shell.css';

export function AppShell({ demo = false }: { demo?: boolean }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { t, lang } = useI18n();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(() => searchParams.has('new-inspection'));
  // The demo lives only at /inspections/demo, never mixed into API inspections.
  const showDemo = demo;
  const inspections = useInspections(!showDemo);
  const demoData = useMemo(() => (showDemo ? createDemo(lang) : null), [showDemo, lang]);
  const sites = useMemo(
    () => demoData?.sites ?? inspections.data ?? [],
    [demoData, inspections.data],
  );
  const selected =
    demoData?.inspection ??
    sites.flatMap((site) => site.inspections).find((inspection) => inspection.id === id);
  const site = sites.find((item) => item.id === selected?.siteId);
  const closeNavigation = () => setSidebarOpen(false);
  const closeDialog = () => {
    setDialogOpen(false);
    if (searchParams.has('new-inspection')) {
      const next = new URLSearchParams(searchParams);
      next.delete('new-inspection');
      setSearchParams(next, { replace: true });
    }
  };

  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = t('shell.documentTitle');
  }, [lang, t]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [sidebarOpen]);

  return (
    <div className={`app-shell${sidebarOpen ? ' sidebar-open' : ''}`}>
      <a className="skip-link" href="#inspection-main">
        {t('shell.skip')}
      </a>
      <div className="sidebar-container" id="inspection-sidebar">
        <Sidebar
          sites={sites}
          demo={showDemo}
          loading={!showDemo && inspections.isPending}
          onNavigate={closeNavigation}
          onNewInspection={() => {
            closeNavigation();
            setDialogOpen(true);
          }}
        />
      </div>
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label={t('shell.closeSidebar')}
          onClick={closeNavigation}
        />
      )}
      <div className="shell-content">
        <header className="shell-topbar">
          <button
            className="shell-button icon-button sidebar-trigger"
            aria-label={t(sidebarOpen ? 'shell.closeSidebar' : 'shell.openSidebar')}
            aria-expanded={sidebarOpen}
            aria-controls="inspection-sidebar"
            onClick={() => setSidebarOpen((open) => !open)}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path d="M3 5h14M3 10h14M3 15h14" />
            </svg>
          </button>
          <div className="breadcrumb">
            <span>{t('shell.controlCenter')}</span>
            {site && (
              <>
                <span aria-hidden="true">/</span>
                <span>{site.name || t('site.unassigned')}</span>
              </>
            )}
          </div>
          {showDemo && <span className="demo-badge">{t('shell.demo')}</span>}
        </header>
        <main id="inspection-main" tabIndex={-1} className="shell-main">
          {!showDemo && inspections.isError && (
            <div className="api-error" role="alert">
              <div>
                <strong>{t('shell.connectionError')}</strong>
                <p>
                  {t(
                    inspections.error.message === '401'
                      ? 'shell.authError'
                      : 'shell.connectionHint',
                  )}
                </p>
              </div>
              <button
                className="shell-button"
                disabled={inspections.isFetching}
                onClick={() => void inspections.refetch()}
              >
                {t('shell.retry')}
              </button>
            </div>
          )}
          {selected ? (
            <InspectionView
              key={selected.id}
              inspection={selected}
              run={demoData?.run}
              demoBundle={demoData?.bundle}
            />
          ) : id ? (
            <div className="empty-state">
              <h1>
                {t(
                  inspections.isPending
                    ? 'shell.loading'
                    : inspections.isError
                      ? 'inspection.unavailable'
                      : 'inspection.notFound',
                )}
              </h1>
              <p>{t(inspections.isError ? 'shell.connectionHint' : 'inspection.selectOther')}</p>
            </div>
          ) : (
            <div className="empty-state">
              <div className="empty-symbol" aria-hidden="true">
                <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.25">
                  <path d="m24 6 16 9v18l-16 9-16-9V15zM8 15l16 9 16-9M24 24v18" />
                  <circle cx="24" cy="24" r="4" fill="currentColor" />
                </svg>
              </div>
              <p className="eyebrow">{t('shell.product')}</p>
              <h1>{t('shell.emptyTitle')}</h1>
              <p>{t('shell.emptyDescription')}</p>
              <button className="shell-button primary" onClick={() => setDialogOpen(true)}>
                <span aria-hidden="true">+</span> {t('inspection.new')}
              </button>
            </div>
          )}
        </main>
      </div>
      {dialogOpen && (
        <NewInspectionDialog
          onClose={closeDialog}
          onUploaded={(inspectionId) => {
            void queryClient.invalidateQueries({ queryKey: ['inspections'] });
            navigate(`/inspections/${inspectionId}`);
          }}
        />
      )}
    </div>
  );
}
