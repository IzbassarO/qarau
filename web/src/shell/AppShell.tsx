import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useI18n } from '../i18n';
import { createDemo } from '../fixtures/demo';
import { useInspections } from '../lib/inspections';
import { Sidebar } from './Sidebar';
import { InspectionsStartPage } from './InspectionsStartPage';
import { inspectionFixtureSites } from '../fixtures/inspections';
import { InspectionView } from './InspectionView';
import { NewInspectionDialog } from './NewInspectionDialog';
import { Wordmark } from './ShellIdentity';
import './shell.css';

export function AppShell({ demo = false }: { demo?: boolean }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { t, lang } = useI18n();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(() => searchParams.has('new-inspection'));
  const listDemo = !id && !demo && searchParams.get('demo') === 'inspections';
  const showDemo = demo || listDemo;
  const inspections = useInspections(!showDemo);
  const demoData = useMemo(() => (demo ? createDemo(lang) : null), [demo, lang]);
  const sites = useMemo(
    () => (listDemo ? inspectionFixtureSites : (demoData?.sites ?? inspections.data ?? [])),
    [listDemo, demoData, inspections.data],
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
    <div
      className={`qv qv-app app-shell${sidebarOpen ? ' sidebar-open' : ''}${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}
    >
      <a className="qv-btn skip-link" href="#inspection-main">
        {t('shell.skip')}
      </a>
      <div className="sidebar-container" id="inspection-sidebar">
        <Sidebar
          sites={sites}
          newInspectionPrimary={!!id || demo}
          demo={showDemo}
          loading={!showDemo && inspections.isPending}
          onNavigate={closeNavigation}
          onCollapse={() => {
            setSidebarCollapsed(true);
            closeNavigation();
          }}
          onNewInspection={() => {
            closeNavigation();
            setDialogOpen(true);
          }}
        />
      </div>
      {sidebarOpen && (
        <button
          className="qv-scrim sidebar-backdrop"
          aria-label={t('shell.closeSidebar')}
          onClick={closeNavigation}
        />
      )}
      <div className="qv-main shell-content">
        <header className="qv-topbar shell-topbar">
          <span className="shell-phone-brand">
            <Wordmark />
          </span>
          <button
            className="qv-iconbtn icon-button sidebar-trigger"
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
          <div className="qv-topbar__title qv-caption breadcrumb">
            <span>{t('shell.controlCenter')}</span>
            {site && (
              <>
                <span aria-hidden="true">/</span>
                <span>{site.name || t('site.unassigned')}</span>
              </>
            )}
          </div>
          {showDemo && <span className="qv-caption demo-badge">{t('shell.demo')}</span>}
        </header>
        <main id="inspection-main" tabIndex={-1} className="qv-page shell-main">
          {id && !showDemo && inspections.isError && (
            <div className="qv-notice api-error" role="alert">
              <div>
                <strong className="qv-strong">{t('shell.connectionError')}</strong>
                <p className="qv-caption">
                  {t(
                    inspections.error.message === '401'
                      ? 'shell.authError'
                      : 'shell.connectionHint',
                  )}
                </p>
              </div>
              <button
                className="qv-btn qv-btn--secondary"
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
            <div className="qv-card">
              <div className="qv-empty empty-state">
                <h1 className="qv-heading">
                  <b>
                    {t(
                      inspections.isPending
                        ? 'shell.loading'
                        : inspections.isError
                          ? 'inspection.unavailable'
                          : 'inspection.notFound',
                    )}
                  </b>
                </h1>
                <p>{t(inspections.isError ? 'shell.connectionHint' : 'inspection.selectOther')}</p>
              </div>
            </div>
          ) : (
            <InspectionsStartPage demo={listDemo} onNewInspection={() => setDialogOpen(true)} />
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
