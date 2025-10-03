<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import { createExternalMemory } from '$lib/apis/memories';

	const dispatch = createEventDispatcher();

	export let show = false;
	export let categories: string[] = [];

	let loading = false;
	let content = '';
	let selectedCategory: string | null = null;
	let useCustomCategory = false;
	let customCategory = '';

	$: sortedCategories = [...new Set(categories)].sort((a, b) =>
		a.localeCompare(b, undefined, { sensitivity: 'base' })
	);

	const resetForm = () => {
		content = '';
		selectedCategory = null;
		useCustomCategory = false;
		customCategory = '';
	};

	const handleClose = () => {
		show = false;
		resetForm();
	};

	const selectCategory = (category: string | null) => {
		selectedCategory = category;
		useCustomCategory = false;
		customCategory = '';
	};

	const enableCustomCategory = () => {
		useCustomCategory = true;
		selectedCategory = null;
	};

	const submitHandler = async () => {
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

		const res = await createExternalMemory(localStorage.token, {
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

		toast.success('Memoria creada.');
		resetForm();
		show = false;
		dispatch('save');
	};
</script>

<Modal bind:show size="sm">
	<div>
		<div class="flex justify-between px-5 pt-4 pb-2">
			<div class="text-lg font-medium self-center">Agregar memoria</div>
			<button class="self-center" on:click={handleClose}>
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
					placeholder="Describe la memoria que quieres guardar"
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
						on:click={() => selectCategory(null)}
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
							on:click={() => selectCategory(categoryOption)}
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
						on:click={enableCustomCategory}
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

				{#if !useCustomCategory && sortedCategories.length === 0}
					<div class="text-xs text-gray-500">
						Aún no hay categorías guardadas. Puedes crear una nueva arriba.
					</div>
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
					Guardar
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
