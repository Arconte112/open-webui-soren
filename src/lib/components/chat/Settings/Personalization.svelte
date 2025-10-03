<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	import ManageModal from './Personalization/ManageModal.svelte';

	const dispatch = createEventDispatcher();

	export let saveSettings: Function;

	const MEMORIES_VARIABLE = '{{MEMORIES}}';

	let showManageModal = false;

	$: void saveSettings;
</script>

<ManageModal bind:show={showManageModal} />

<form
	id="tab-personalization"
	class="flex flex-col h-full justify-between space-y-4 text-sm"
	on:submit|preventDefault={() => {
		dispatch('save');
	}}
>
	<div class="py-1 overflow-y-auto max-h-[28rem] md:max-h-full space-y-4">
		<div class="space-y-1">
			<div class="text-sm font-medium">Memorias personalizadas</div>
			<div class="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
				Gestiona las memorias almacenadas en tu base de datos externa, organiza el contenido por categorías y mantén sincronizada la variable
				<span class="font-mono text-[0.7rem]">{MEMORIES_VARIABLE}</span> con lo que necesites que el asistente recuerde.
			</div>
		</div>

		<div>
			<button
				type="button"
				class="px-3.5 py-1.5 font-medium hover:bg-black/5 dark:hover:bg-white/5 outline outline-1 outline-gray-300 dark:outline-gray-800 rounded-3xl"
				on:click={() => {
					showManageModal = true;
				}}
			>
				Abrir gestor de memorias
			</button>
		</div>
	</div>

	<div class="flex justify-end text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			Guardar
		</button>
	</div>
</form>
