<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';
	import localizedFormat from 'dayjs/plugin/localizedFormat';

	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import AddMemoryModal from './AddMemoryModal.svelte';
	import EditMemoryModal from './EditMemoryModal.svelte';

	import {
		deleteExternalMemory,
		getExternalMemories,
		type MemoryEntry
	} from '$lib/apis/memories';

	dayjs.extend(localizedFormat);

	const UNCATEGORIZED_KEY = '__UNCATEGORIZED__';

	export let show = false;

	const dispatch = createEventDispatcher();

	let loading = false;
	let memories: MemoryEntry[] = [];
	let activeTab: string = 'all';
	let selectedMemory: MemoryEntry | null = null;
	let showAddMemoryModal = false;
	let showEditMemoryModal = false;
	let availableCategories: string[] = [];

	const refreshMemories = async (withSpinner = true) => {
		if (withSpinner) {
			loading = true;
		}

		const data = await getExternalMemories(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (data) {
			memories = data;
		}

		loading = false;
	};

	let shouldRefreshOnOpen = true;

	$: if (show && shouldRefreshOnOpen) {
		shouldRefreshOnOpen = false;
		refreshMemories(true);
	}

	$: if (!show) {
		shouldRefreshOnOpen = true;
	}

	$: categoryTabs = Array.from(
		new Set(
			memories.map((memory) => {
				const value = memory.category?.trim();
				return value && value.length > 0 ? value : UNCATEGORIZED_KEY;
			})
		)
	)
		.sort((a, b) => {
			if (a === UNCATEGORIZED_KEY) return -1;
			if (b === UNCATEGORIZED_KEY) return 1;
			return a.localeCompare(b, undefined, { sensitivity: 'base' });
		})
		.map((key) => ({
			key,
			label: key === UNCATEGORIZED_KEY ? 'Sin categoría' : key
		}));

	$: availableCategories = Array.from(
		new Set(
			memories
				.map((memory) => memory.category?.trim())
				.filter((category): category is string => !!category && category.length > 0)
		)
	)
		.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

	$: if (activeTab !== 'all') {
		const exists = categoryTabs.some((tab) => tab.key === activeTab);
		if (!exists) {
			activeTab = 'all';
		}
	}

	$: visibleMemories = memories.filter((memory) => {
		if (activeTab === 'all') {
			return true;
		}

		const categoryKey = memory.category?.trim()
			? memory.category.trim()
			: UNCATEGORIZED_KEY;

		return categoryKey === activeTab;
	});

	const handleDelete = async (memory: MemoryEntry) => {
		const confirmed =
			typeof window === 'undefined'
				? true
				: window.confirm('¿Eliminar esta memoria?');
		if (!confirmed) {
			return;
		}

		const res = await deleteExternalMemory(localStorage.token, memory.id).catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);

		if (!res) {
			return;
		}

		toast.success('Memoria eliminada.');
		await refreshMemories(false);
	};

	const openEditModal = (memory: MemoryEntry) => {
		selectedMemory = memory;
		showEditMemoryModal = true;
	};

	const handleSaved = async () => {
		await refreshMemories(false);
		dispatch('updated');
	};

	$: if (!showEditMemoryModal) {
		selectedMemory = null;
	}

	const formatDate = (value?: string | null) => {
		if (!value) {
			return '';
		}
		return dayjs(value).format('LLL');
	};
</script>

<Modal size="xl" bind:show>
	<div class="flex flex-col max-h-[32rem]">
		<div class="flex items-center justify-between px-5 pt-4 pb-2">
			<div class="text-lg font-medium">Memorias</div>
			<div class="flex items-center space-x-2">
				<button
					class="rounded-full p-2 hover:bg-black/5 dark:hover:bg-white/5"
					on:click={() => {
						showAddMemoryModal = true;
					}}
					title="Agregar memoria"
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-5 h-5">
						<path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
					</svg>
				</button>
				<button
					class="rounded-full p-2 hover:bg-black/5 dark:hover:bg-white/5"
					on:click={() => {
						show = false;
					}}
					title="Cerrar"
				>
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-5 h-5">
						<path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
					</svg>
				</button>
			</div>
		</div>

		<div class="px-5 pb-4 overflow-y-auto">
			<div class="flex flex-wrap gap-2 text-sm mb-4">
				<button
					class={`px-3 py-1.5 rounded-full border ${
						activeTab === 'all'
							? 'border-black dark:border-white bg-black/90 text-white dark:bg-white/90 dark:text-black'
							: 'border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5'
					}`}
					on:click={() => (activeTab = 'all')}
				>
					Todas
				</button>

				{#each categoryTabs as tab}
					<button
						class={`px-3 py-1.5 rounded-full border ${
							activeTab === tab.key
								? 'border-black dark:border-white bg-black/90 text-white dark:bg-white/90 dark:text-black'
								: 'border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5'
						}`}
						on:click={() => (activeTab = tab.key)}
					>
						{tab.label}
					</button>
				{/each}
			</div>

			{#if loading}
				<div class="flex justify-center py-16">
					<Spinner />
				</div>
			{:else if visibleMemories.length === 0}
				<div class="text-sm text-gray-500 py-16 text-center">
					No hay memorias guardadas todavía.
				</div>
			{:else}
				<div class="space-y-3">
					{#each visibleMemories as memory (memory.id)}
						<div class="border border-gray-100 dark:border-gray-800 rounded-xl p-4">
							<div class="flex items-start justify-between gap-3">
								<div class="flex-1 space-y-2">
									{#if memory.category}
										<div class="text-xs uppercase tracking-wide text-gray-400">
											{memory.category}
										</div>
									{:else}
										<div class="text-xs uppercase tracking-wide text-gray-400">Sin categoría</div>
									{/if}
									<div class="text-sm leading-relaxed whitespace-pre-wrap">
										{memory.content}
									</div>
									{#if memory.updated_at || memory.created_at}
										<div class="text-xs text-gray-500">
											Última actualización: {formatDate(memory.updated_at ?? memory.created_at)}
										</div>
									{/if}
								</div>
								<div class="flex items-center space-x-1">
									<Tooltip content="Editar">
										<button
											class="p-2 rounded-full hover:bg-black/5 dark:hover:bg-white/5"
											on:click={() => openEditModal(memory)}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 24 24"
												fill="none"
												stroke-width="1.5"
												stroke="currentColor"
												class="w-4 h-4"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125"
												/>
											</svg>
										</button>
									</Tooltip>

									<Tooltip content="Eliminar">
										<button
											class="p-2 rounded-full hover:bg-black/5 dark:hover:bg-white/5"
											on:click={() => handleDelete(memory)}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												stroke="currentColor"
												class="w-4 h-4"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
												/>
											</svg>
										</button>
									</Tooltip>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</Modal>

<AddMemoryModal
	bind:show={showAddMemoryModal}
	categories={availableCategories}
	on:save={handleSaved}
/>

<EditMemoryModal
	bind:show={showEditMemoryModal}
	memory={selectedMemory}
	categories={availableCategories}
	on:save={handleSaved}
/>
