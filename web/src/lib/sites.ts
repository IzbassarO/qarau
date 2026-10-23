import { readApi } from './api';

export interface SiteOption {
  id: number;
  org_id: number;
  name: string;
}

export async function listSites(signal?: AbortSignal): Promise<SiteOption[]> {
  const sites = await readApi('/api/sites', signal);
  if (
    !Array.isArray(sites) ||
    !sites.every((site) => Number.isInteger(site?.id) && typeof site.name === 'string')
  )
    throw new Error('Invalid sites response');
  return sites as SiteOption[];
}
