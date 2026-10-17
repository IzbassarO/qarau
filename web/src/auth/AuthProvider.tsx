import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { login, logout, sessionHeader, subscribeToAuth, type Credentials } from './auth';

interface AuthContextValue {
  signedIn: boolean;
  signIn: (credentials: Credentials) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const header = useSyncExternalStore(subscribeToAuth, sessionHeader, () => null);

  return (
    <AuthContext
      value={{
        signedIn: header !== null,
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

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('AuthProvider is required');
  return context;
}
