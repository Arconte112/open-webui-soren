import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ChatSummarySettings = {
	enabled: boolean;
	model_id: string | null;
	api_key: string | null;
	prompt: string;
	max_items: number;
	updated_at?: string | null;
};

export type ChatSummaryItem = {
	chat_id: string;
	summary: string | null;
	created_at: string;
	expires_at: string;
};

const request = async <T>(token: string, path: string, init: RequestInit): Promise<T> => {
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

export const getChatSummarySettings = (token: string) =>
	request<ChatSummarySettings>(token, '/chat-summaries/config', { method: 'GET' });

export const saveChatSummarySettings = (
	token: string,
	payload: Partial<ChatSummarySettings>
) =>
	request<ChatSummarySettings>(token, '/chat-summaries/config', {
		method: 'POST',
		body: JSON.stringify(payload)
	});

export const getChatSummaries = (token: string, limit = 20) =>
	request<ChatSummaryItem[]>(token, `/chat-summaries/?limit=${limit}`, { method: 'GET' });
