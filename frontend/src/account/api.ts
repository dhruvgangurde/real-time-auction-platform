// The account's calls (FR-9, FR-10) on the shared client; they live in the account chunk.
import type { Api } from '../api/client';

/** FR-9: only the name and avatar are the user's to change; the avatar is not offered (gap C1). */
export async function updateName(api: Api, name: string): Promise<void> {
  await api.request('PATCH', '/users/me', { auth: 'required', body: { full_name: name } });
}
