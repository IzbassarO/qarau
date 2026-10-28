import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  fetchSessionRole,
  login,
  logout,
  sessionHeader,
  subscribeToAuth,
  type Credentials,
  type Role,
} from './auth';

interface AuthContextValue {
  signedIn: boolean;
  role: Role | null;
  roleError: boolean;
  loadRole: () => Promise<void>;
  signIn: (credentials: Credentials) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const header = useSyncExternalStore(subscribeToAuth, sessionHeader, () => null);
  const [identity, setIdentity] = useState<{
    session: string;
    role: Role | null;
    error: boolean;
  }>();
  // Resolve only when a role-aware view is mounted. Offline demos need no account reads.
  const loadRole = useCallback(async () => {
    if (header === null) return;
    try {
      const role = await queryClient.fetchQuery({
        queryKey: ['session-role'],
        queryFn: ({ signal }) => fetchSessionRole(signal),
        retry: false,
        staleTime: 30_000,
      });
      if (sessionHeader() === header) setIdentity({ session: header, role, error: false });
    } catch {
      if (sessionHeader() === header) setIdentity({ session: header, role: null, error: true });
    }
  }, [header, queryClient]);
  const current = identity?.session === header ? identity : undefined;

  return (
    <AuthContext
      value={{
        signedIn: header !== null,
        role: header === null ? 'viewer' : (current?.role ?? null),
        roleError: header !== null && current?.error === true,
        loadRole,
        signIn: async (credentials) => {
          queryClient.clear();
          await login(credentials);
        },
        signOut: () => {
          logout();
          queryClient.clear();
        },
      }}
    >
      {children}
    </AuthContext>
  );
}

export function useAuth({ resolveRole = false } = {}): AuthContextValue {
  const context = useContext(AuthContext);
  const loadRole = context?.loadRole;
  useEffect(() => {
    if (resolveRole && loadRole) void loadRole();
  }, [resolveRole, loadRole]);
  if (!context) throw new Error('AuthProvider is required');
  return context;
}
