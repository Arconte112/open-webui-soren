<script lang="ts">
	import { onMount } from 'svelte';
	import dayjs from 'dayjs';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	import { toast } from 'svelte-sonner';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getChatSummaries,
		getChatSummarySettings,
		saveChatSummarySettings,
		type ChatSummaryItem,
		type ChatSummarySettings
	} from '$lib/apis/chat-summaries';

	dayjs.extend(localizedFormat);

	const DEFAULT_PROMPT =
		'Eres un asistente que resume conversaciones. Devuelve un resumen en máximo 2 líneas. Si no hay contexto útil, responde solo con la palabra null.';

let enabled = false;
let modelId = '';
let apiKey = '';
let prompt = DEFAULT_PROMPT;
let maxItems = 15;
let updatedAt: string | null = null;

	let loading = true;
	let saving = false;
	let summariesLoading = false;
	let summaries: ChatSummaryItem[] = [];

	const fetchSettings = async () => {
		loading = true;
		const data = await getChatSummarySettings(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (data) {
			enabled = data.enabled ?? false;
			modelId = data.model_id ?? '';
			apiKey = data.api_key ?? '';
			prompt = data.prompt ?? DEFAULT_PROMPT;
			maxItems = data.max_items ?? 15;
			updatedAt = data.updated_at ?? null;
		}
		loading = false;
	};

	const fetchSummaries = async () => {
		summariesLoading = true;
		const data = await getChatSummaries(localStorage.token, maxItems || 15).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (data) {
			summaries = data;
		}
		summariesLoading = false;
	};

	const handleSave = async () => {
		saving = true;
		const payload: Partial<ChatSummarySettings> = {
			enabled,
			model_id: modelId.trim(),
			api_key: apiKey.trim(),
			prompt: prompt.trim(),
			max_items: maxItems
		};

		const data = await saveChatSummarySettings(localStorage.token, payload).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (data) {
			toast.success('Configuración guardada');
			enabled = data.enabled ?? false;
			modelId = data.model_id ?? '';
			apiKey = data.api_key ?? '';
			prompt = data.prompt ?? DEFAULT_PROMPT;
			updatedAt = data.updated_at ?? null;
		}

		saving = false;
	};

	onMount(async () => {
		await fetchSettings();
		await fetchSummaries();
	});

	const formatDate = (value?: string | null) => {
		if (!value) return '';
		return dayjs(value).format('LLL');
	};
</script>

<div class="space-y-4">
	<div class="flex items-start justify-between gap-3">
		<div class="space-y-1">
			<div class="text-sm font-medium">Resumen de chats</div>
			<div class="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
				Genera resúmenes automáticos de chats inactivos (>10 minutos) y los guarda de forma indefinida en la
				base externa. Usa tu modelo de OpenRouter y un prompt editable para ajustar el estilo.
			</div>
			{#if updatedAt}
				<div class="text-xs text-gray-500">Última edición: {formatDate(updatedAt)}</div>
			{/if}
		</div>
		<label class="flex items-center space-x-2 text-xs font-medium select-none">
			<input
				type="checkbox"
				class="w-4 h-4 rounded border-gray-300 dark:border-gray-700"
				bind:checked={enabled}
				disabled={loading}
			/>
			<span>Activar</span>
		</label>
	</div>

	<div class="grid md:grid-cols-2 gap-3">
		<div class="space-y-1">
			<label class="text-xs uppercase tracking-wide text-gray-500">Model ID</label>
			<input
				class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm"
				placeholder="p. ej. openrouter/anthropic/claude-3.5-sonnet"
				bind:value={modelId}
				autocomplete="off"
				disabled={loading}
			/>
		</div>
		<div class="space-y-1">
			<label class="text-xs uppercase tracking-wide text-gray-500">API Key (OpenRouter)</label>
			<input
				class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm"
				type="password"
				placeholder="sk-or-v1-..."
				bind:value={apiKey}
				autocomplete="off"
				disabled={loading}
			/>
		</div>
		<div class="space-y-1">
			<label class="text-xs uppercase tracking-wide text-gray-500">Cantidad de resúmenes</label>
			<input
				class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm"
				type="number"
				min="1"
				max="200"
				bind:value={maxItems}
				disabled={loading}
			/>
			<div class="text-[0.7rem] text-gray-500">
				Límite de resúmenes recientes que se guardan y se exponen en <code>{'{{CHAT_SUMMARIES}}'}</code>.
			</div>
		</div>
	</div>

	<div class="space-y-1">
		<label class="text-xs uppercase tracking-wide text-gray-500">Prompt de resumen</label>
		<textarea
			class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm min-h-[96px]"
			placeholder={DEFAULT_PROMPT}
			bind:value={prompt}
			disabled={loading}
		/>
		<div class="text-[0.7rem] text-gray-500">
			El modelo puede devolver <code>null</code> si considera que el chat no aporta contexto futuro.
		</div>
	</div>

	<div class="flex items-center space-x-2">
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-60"
			on:click={handleSave}
			disabled={loading || saving}
		>
			{saving ? 'Guardando...' : 'Guardar configuración'}
		</button>
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-medium hover:bg-black/5 dark:hover:bg-white/5 rounded-full disabled:opacity-60"
			on:click={fetchSummaries}
			disabled={summariesLoading}
		>
			{summariesLoading ? 'Actualizando...' : 'Refrescar lista'}
		</button>
	</div>

	<div class="space-y-2">
		<div class="flex items-center justify-between">
			<div class="text-sm font-medium">Últimos resúmenes ({maxItems || 15})</div>
			{#if summariesLoading}
				<div class="text-xs text-gray-500">Cargando…</div>
			{/if}
		</div>

		{#if summariesLoading}
			<div class="flex justify-center py-10">
				<Spinner />
			</div>
		{:else if summaries.length === 0}
			<div class="text-sm text-gray-500 py-6 text-center">
				Todavía no hay resúmenes generados.
			</div>
		{:else}
			<div class="space-y-3">
				{#each summaries as item (item.chat_id)}
					<div class="border border-gray-100 dark:border-gray-800 rounded-xl p-4 space-y-2">
						<div class="flex items-center justify-between text-xs text-gray-500">
							<span class="font-mono text-[0.75rem]">{item.chat_id}</span>
							<span>{formatDate(item.created_at)}</span>
						</div>
						<div class="text-sm leading-relaxed whitespace-pre-line">
							{item.summary ?? 'null (sin contenido relevante)'}
						</div>
						{#if item.expires_at}
							<div class="text-[0.7rem] text-gray-500">
								Conservado (fecha de retención): {formatDate(item.expires_at)}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
