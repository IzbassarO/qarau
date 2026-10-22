import { useState } from 'react';
import { Link, NavLink } from 'react-router';
import { useI18n } from '../i18n';
import { UNASSIGNED_SITE_ID, type Site } from '../lib/inspections';
import { LanguageSwitch, Wordmark } from './ShellIdentity';
import { useAuth } from '../auth/AuthProvider';

/** "16 Oct" / "16 окт." — day first in every UI language. */
export function shortDate(iso: string, lang: string): string {
  return new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : lang, {
    day: 'numeric',
    month: 'short',
  }).format(new Date(iso));
}

interface SidebarProps {
  sites: Site[];
  onNewInspection: () => void;
  onNavigate: () => void;
  demo?: boolean;
  loading?: boolean;
}

export function Sidebar({ sites, onNewInspection, onNavigate, demo, loading }: SidebarProps) {
  const { t, lang } = useI18n();
  const { signOut } = useAuth();
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
        <Wordmark />
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
              <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor">
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
                    title={inspection.source ?? inspection.title}
                  >
                    <span className={`status-dot ${inspection.status}`} aria-hidden="true" />
                    <span className="inspection-link-title">
                      <span className="inspection-link-label">
                        <span className="inspection-link-name" title={inspection.title}>
                          {inspection.title}
                        </span>
                        {demo && inspection.id === 'demo' && (
                          <span className="demo-badge">{t('shell.demo')}</span>
                        )}
                      </span>
                      {inspection.createdAt && (
                        <time className="inspection-link-date" dateTime={inspection.createdAt}>
                          {shortDate(inspection.createdAt, lang)}
                        </time>
                      )}
                    </span>
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
          {t('shell.openDemo')}
        </Link>
        <LanguageSwitch />
        <button type="button" className="shell-button" onClick={signOut}>
          {t('auth.signOut')}
        </button>
      </footer>
    </nav>
  );
}
