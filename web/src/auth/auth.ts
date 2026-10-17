export interface Credentials {
  username: string;
  password: string;
}

export class AuthError extends Error {
  constructor(public code: 'required' | 'invalidCredentials' | 'connection' | 'storage') {
    super(code);
  }
}

const SESSION_KEY = 'qarau.auth.session';
const subscribers = new Set<() => void>();

export function sessionHeader(): string | null {
  try {
    const header = sessionStorage.getItem(SESSION_KEY);
    return header?.startsWith('Bearer ') && header.length > 7 ? header : null;
  } catch {
    return null;
  }
}

export function subscribeToAuth(callback: () => void) {
  subscribers.add(callback);
  return () => {
    subscribers.delete(callback);
  };
}

export function authHeader(): Record<string, string> {
  const header = sessionHeader();
  return header ? { Authorization: header } : {};
}

export function logout(): void {
  sessionStorage.removeItem(SESSION_KEY);
  subscribers.forEach((callback) => callback());
}

/** The backend has a shared Bearer token, not individual username/password accounts. */
export async function login({ username, password }: Credentials): Promise<void> {
  try {
    logout();
  } catch {
    throw new AuthError('storage');
  }
  if (!username.trim() || !password.trim()) throw new AuthError('required');
  const header = `Bearer ${password}`;
  let response: Response;
  try {
    response = await fetch('/api/runs', {
      headers: { Authorization: header },
      cache: 'no-store',
      redirect: 'error',
    });
  } catch {
    throw new AuthError('connection');
  }
  if (response.status === 401 || response.status === 403) throw new AuthError('invalidCredentials');
  if (response.status !== 200) throw new AuthError('connection');
  try {
    sessionStorage.setItem(SESSION_KEY, header);
  } catch {
    throw new AuthError('storage');
  }
  subscribers.forEach((callback) => callback());
}
