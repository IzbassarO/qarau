import { queryOptions } from '@tanstack/react-query';
import { readApi } from './api';

export type Role = 'viewer' | 'engineer' | 'admin';

export interface Me {
  id: number;
  name: string;
  role: Role;
  organization: { id: number; name: string };
}

/** ME1 supplies permissions explicitly; unknown roles never grant decision access. */
export async function getMe(signal?: AbortSignal): Promise<Me> {
  const body = await readApi('/api/me', signal);
  if (body && typeof body === 'object' && 'role' in body) {
    if (body.role === 'viewer' || body.role === 'engineer' || body.role === 'admin')
      return body as Me;
  }
  throw new Error('Unknown account role');
}

export const meOptions = () =>
  queryOptions({
    queryKey: ['me'],
    queryFn: ({ signal }) => getMe(signal),
    retry: false,
    staleTime: 30_000,
  });
