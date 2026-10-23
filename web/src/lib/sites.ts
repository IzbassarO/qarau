import { authHeader } from '../auth/auth';

export interface SiteOption {
  id: number;
  org_id: number;
  name: string;
}

export async function listSites(signal?: AbortSignal): Promise<SiteOption[]> {
  const response = await fetch('/api/sites', { signal, headers: authHeader() });
  if (!response.ok) throw new Error(String(response.status));
  const sites: unknown = await response.json();
  if (
    !Array.isArray(sites) ||
    !sites.every((site) => Number.isInteger(site?.id) && typeof site.name === 'string')
  )
    throw new Error('Invalid sites response');
  return sites as SiteOption[];
}
