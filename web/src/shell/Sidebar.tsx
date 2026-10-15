import { useState } from 'react';
import { Link, NavLink } from 'react-router';
import { useI18n } from '../i18n';
import { UNASSIGNED_SITE_ID, type Site } from '../lib/inspections';
import { LangSwitch, Logo } from '../viewer/ui';

interface SidebarProps {
  sites: Site[];
  onNewInspection: () => void;
  onNavigate: () => void;
  demo?: boolean;
  loading?: boolean;
}

export function Sidebar({ sites, onNewInspection, onNavigate, demo, loading }: SidebarProps) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (id: string) => {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <nav className="shell-sidebar" aria-label={t('shell.sites')}>
      <Link to="/" className="sidebar-brand" aria-label={t('shell.home')} onClick={onNavigate}>
        <Logo />
      </Link>
      <button className="shell-button primary new-inspection" onClick={onNewInspection}>
        <span aria-hidden="true">+</span> {t('inspection.new')}
      </button>
      <div className="sidebar-heading">
        <h2>{t('shell.sites')}</h2>
        {demo && <span className="demo-badge">{t('shell.demo')}</span>}
      </div>
      <div className="sidebar-groups">
        {loading && (
          <p role="status" className="sidebar-note">
            {t('shell.loading')}
          </p>
        )}
        {!loading && sites.length === 0 && <p className="sidebar-note">{t('shell.noSites')}</p>}
        {sites.map((site, index) => (
          <section className="site-group" key={site.id}>
            <button
              className="site-toggle"
              onClick={() => toggle(site.id)}
              aria-expanded={!collapsed.has(site.id)}
              aria-controls={`site-inspections-${index}`}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 16 16"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
              >
                <path d={collapsed.has(site.id) ? 'm6 4 4 4-4 4' : 'm4 6 4 4 4-4'} />
              </svg>
              <span className="site-name">
                {site.id === UNASSIGNED_SITE_ID
                  ? t('site.unassigned')
                  : site.name || t('site.unnamed')}
              </span>
              <span className="site-count">{site.inspections.length}</span>
            </button>
            <ul id={`site-inspections-${index}`} hidden={collapsed.has(site.id)}>
              {site.inspections.map((inspection) => (
                <li key={inspection.id}>
                  <NavLink
                    to={`/inspections/${encodeURIComponent(inspection.id)}`}
                    className={({ isActive }) => `inspection-link${isActive ? ' active' : ''}`}
                    onClick={onNavigate}
                    title={inspection.title}
                  >
                    <span className={`status-dot ${inspection.status}`} aria-hidden="true" />
                    <span className="inspection-link-title">{inspection.title}</span>
                    <span className="sr-only">{t(`status.${inspection.status}`)}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <footer className="sidebar-footer">
        <Link to="/inspections/demo" onClick={onNavigate} className="demo-link">
          {t('shell.openDemo')} <span className="demo-badge">{t('shell.demo')}</span>
        </Link>
        <LangSwitch />
      </footer>
    </nav>
  );
}
