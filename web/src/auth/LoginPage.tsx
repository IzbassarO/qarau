import { useEffect, useState, type FormEvent } from 'react';
import { useI18n, type Key } from '../i18n';
import { LangSwitch, Logo } from '../viewer/ui';
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
    <main className="login-page">
      <header className="login-header">
        <Logo />
        <LangSwitch />
      </header>
      <section className="login-card" aria-labelledby="login-title">
        <p className="eyebrow">{t('shell.product')}</p>
        <h1 id="login-title">{t('auth.title')}</h1>
        <p className="login-description">{t('auth.description')}</p>
        <form onSubmit={(event) => void submit(event)} aria-label={t('auth.title')}>
          <label htmlFor="login-username">{t('auth.username')}</label>
          <input
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
          <label htmlFor="login-password">{t('auth.password')}</label>
          <input
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
          {error && (
            <p className="login-error" id="login-error" role="alert">
              {t(error)}
            </p>
          )}
          <button className="shell-button primary" type="submit" disabled={pending}>
            {t(pending ? 'auth.signingIn' : 'auth.signIn')}
          </button>
        </form>
      </section>
    </main>
  );
}
