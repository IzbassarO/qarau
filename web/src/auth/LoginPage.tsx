import { useEffect, useState, type FormEvent } from 'react';
import { useI18n, type Key } from '../i18n';
import { LanguageSwitch, Wordmark } from '../shell/ShellIdentity';
import { AuthError } from './auth';
import { useAuth } from './AuthProvider';
import '../shell/shell.css';

export function LoginPage() {
  const { t, lang } = useI18n();
  const { signIn } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Key | null>(null);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = t('auth.documentTitle');
  }, [lang, t]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      await signIn({ username, password });
    } catch (cause) {
      setError(cause instanceof AuthError ? `auth.${cause.code}` : 'auth.connection');
      setPassword('');
    } finally {
      setPending(false);
    }
  };

  return (
    <main className="qv login-page">
      <section className="qv-hero login-hero" aria-labelledby="login-hero-title">
        <Wordmark hero />
        <div className="login-hero-copy">
          <h1 id="login-hero-title">{t('auth.heroTitle')}</h1>
          <p className="sub">{t('auth.heroTagline')}</p>
        </div>
      </section>
      <div className="login-content">
        <header className="login-header">
          <LanguageSwitch />
        </header>
        <section className="qv-card login-card" aria-labelledby="login-title">
          <h2 className="qv-title" id="login-title">
            {t('auth.title')}
          </h2>
          <p className="qv-caption login-description">{t('auth.description')}</p>
          <form
            className="qv-form login-form"
            onSubmit={(event) => void submit(event)}
            aria-label={t('auth.title')}
          >
            <div className="qv-field">
              <label htmlFor="login-username">{t('auth.username')}</label>
              <input
                className="qv-input"
                id="login-username"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                disabled={pending}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
            <div className="qv-field">
              <label htmlFor="login-password">{t('auth.password')}</label>
              <input
                className="qv-input"
                id="login-password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                disabled={pending}
                aria-describedby={error ? 'login-error' : undefined}
                aria-invalid={error === 'auth.invalidCredentials' || undefined}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {error && (
              <p className="qv-notice login-error" id="login-error" role="alert">
                {t(error)}
              </p>
            )}
            <button className="qv-btn qv-btn--lg qv-btn--block" type="submit" disabled={pending}>
              {t(pending ? 'auth.signingIn' : 'auth.signIn')}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
