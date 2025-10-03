<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import { updateExternalMemory, type MemoryEntry } from '$lib/apis/memories';

	const dispatch = createEventDispatcher();

	export let show = false;
	export let memory: MemoryEntry | null = null;
	export let categories: string[] = [];

	let loading = false;
	let content = '';
	let selectedCategory: string | null = null;
	let useCustomCategory = false;
	let customCategory = '';

	$: sortedCategories = [...new Set([...(categories ?? []), memory?.category ?? ''])]
		.map((category) => category.trim())
		.filter((category, index, all) => category.length > 0 && all.indexOf(category) === index)
		.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

	const syncForm = () => {
		content = memory?.content ?? '';
		const categoryValue = memory?.category?.trim() ?? null;
		selectedCategory = categoryValue;
		useCustomCategory = false;
		customCategory = '';
	};

	$: if (show) {
		syncForm();
	}

	const submitHandler = async () => {
		if (!memory) {
			return;
		}

		const trimmedContent = content.trim();
		if (!trimmedContent) {
			toast.error('El contenido no puede estar vacío.');
			return;
		}

		let finalCategory: string | null = null;
		if (useCustomCategory) {
			const trimmedCustomCategory = customCategory.trim();
			if (!trimmedCustomCategory) {
				toast.error('La nueva categoría no puede estar vacía.');
				return;
			}
			finalCategory = trimmedCustomCategory;
		} else {
			finalCategory = selectedCategory;
		}

		loading = true;

		const res = await updateExternalMemory(localStorage.token, memory.id, {
			content: trimmedContent,
			category: finalCategory ? finalCategory : null
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		loading = false;

		if (!res) {
			return;
		}

		toast.success('Memoria actualizada.');
		show = false;
		dispatch('save');
	};
</script>

<Modal bind:show size="sm">
	<div>
		<div class="flex justify-between px-5 pt-4 pb-2">
			<div class="text-lg font-medium self-center">Editar memoria</div>
			<button class="self-center" on:click={() => (show = false)}>
				<XMark className="size-5" />
			</button>
		</div>

		<div class="flex flex-col px-5 pb-4 text-sm space-y-4">
			<label class="flex flex-col space-y-2">
				<span class="text-xs uppercase tracking-wide text-gray-500">Contenido</span>
				<textarea
					class="bg-transparent w-full rounded-xl p-3 outline outline-1 outline-gray-100 dark:outline-gray-800"
					rows="6"
					style="resize: vertical;"
					bind:value={content}
					placeholder="Actualiza la memoria"
				/>
			</label>

			<div class="flex flex-col space-y-2">
				<span class="text-xs uppercase tracking-wide text-gray-500">Categoría</span>
				<div class="flex flex-wrap gap-2">
					<button
						type="button"
						class={`px-3 py-1.5 rounded-full border transition ${
							!useCustomCategory && selectedCategory === null
								? 'border-black dark:border-white bg-black/90 text-white dark:bg-white/90 dark:text-black'
								: 'border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5'
						}`}
						on:click={() => {
							selectedCategory = null;
							useCustomCategory = false;
							customCategory = '';
						}}
					>
						Sin categoría
					</button>

					{#each sortedCategories as categoryOption (categoryOption)}
						<button
							type="button"
							class={`px-3 py-1.5 rounded-full border transition ${
								!useCustomCategory && selectedCategory === categoryOption
									? 'border-black dark:border-white bg-black/90 text-white dark:bg-white/90 dark:text-black'
									: 'border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5'
							}`}
							on:click={() => {
								selectedCategory = categoryOption;
								useCustomCategory = false;
								customCategory = '';
							}}
						>
							{categoryOption}
						</button>
					{/each}

					<button
						type="button"
						class={`px-3 py-1.5 rounded-full border transition ${
							useCustomCategory
								? 'border-black dark:border-white bg-black/90 text-white dark:bg-white/90 dark:text-black'
								: 'border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5'
						}`}
						on:click={() => {
							useCustomCategory = true;
							selectedCategory = null;
						}}
					>
						Nueva categoría
					</button>
				</div>

				{#if useCustomCategory}
					<input
						class="bg-transparent w-full rounded-xl p-3 outline outline-1 outline-gray-100 dark:outline-gray-800"
						type="text"
						bind:value={customCategory}
						placeholder="Escribe el nombre de la nueva categoría"
					/>
				{/if}
			</div>

			<div class="flex justify-end">
				<button
					class={`px-3.5 py-1.5 font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full ${
						loading ? 'cursor-not-allowed opacity-75' : ''
					}`}
					on:click|preventDefault={submitHandler}
					disabled={loading}
				>
					Actualizar
					{#if loading}
						<div class="ml-2 inline-flex">
							<Spinner />
						</div>
					{/if}
				</button>
			</div>
		</div>
	</div>
</Modal>
