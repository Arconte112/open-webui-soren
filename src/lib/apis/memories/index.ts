import { WEBUI_API_BASE_URL } from '$lib/constants';

export type MemoryEntry = {
	id: number;
	content: string;
	category?: string | null;
	importance?: number | null;
	created_at?: string | null;
	updated_at?: string | null;
};

export type CreateMemoryPayload = {
	content: string;
	category?: string | null;
	importance?: number | null;
};

export type UpdateMemoryPayload = {
	content?: string;
	category?: string | null;
	importance?: number | null;
};

const request = async <T>(
	token: string,
	path: string,
	init: RequestInit
): Promise<T> => {
	let error: string | undefined;

	const response = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`,
			...(init.headers ?? {})
		}
	})
		.then(async (res) => {
			if (!res.ok) {
				throw await res.json();
			}
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err?.message ?? String(err);
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return response as T;
};

export const getExternalMemories = (token: string) =>
	request<MemoryEntry[]>(token, '/memories/external', { method: 'GET' });

export const createExternalMemory = (
	token: string,
	payload: CreateMemoryPayload
) =>
	request<MemoryEntry>(token, '/memories/external', {
		method: 'POST',
		body: JSON.stringify(payload)
	});

export const updateExternalMemory = (
	token: string,
	id: number,
	payload: UpdateMemoryPayload
) =>
	request<MemoryEntry>(token, `/memories/external/${id}`, {
		method: 'PUT',
		body: JSON.stringify(payload)
	});

export const deleteExternalMemory = (token: string, id: number) =>
	request<boolean>(token, `/memories/external/${id}`, { method: 'DELETE' });
