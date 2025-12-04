import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ScheduledTasksConfig = {
	model_id: string;
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

export const getScheduledTasksConfig = (token: string) =>
	request<ScheduledTasksConfig>(token, '/scheduled-tasks/config', { method: 'GET' });

export const saveScheduledTasksConfig = (token: string, payload: Partial<ScheduledTasksConfig>) =>
	request<ScheduledTasksConfig>(token, '/scheduled-tasks/config', {
		method: 'POST',
		body: JSON.stringify(payload)
	});
