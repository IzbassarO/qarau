import { Outlet } from 'react-router';
import { useAuth } from './AuthProvider';
import { LoginPage } from './LoginPage';

export function RequireAuth({ allowInDev = false }: { allowInDev?: boolean }) {
  const { signedIn } = useAuth();
  return signedIn || (allowInDev && import.meta.env.DEV) ? <Outlet /> : <LoginPage />;
}
