<script lang="ts">
  import { onMount } from 'svelte';
  import { toast } from 'svelte-sonner';

  import { getScheduledTasksConfig, saveScheduledTasksConfig } from '$lib/apis/scheduled-tasks';

  let modelId = '';
  let loading = true;
  let saving = false;

  const fetchConfig = async () => {
    loading = true;
    const data = await getScheduledTasksConfig(localStorage.token).catch((error) => {
      toast.error(`${error}`);
      return null;
    });

    if (data) {
      modelId = data.model_id ?? '';
    }
    loading = false;
  };

  const handleSave = async () => {
    saving = true;
    const payload = { model_id: modelId.trim() };

    const data = await saveScheduledTasksConfig(localStorage.token, payload).catch((error) => {
      toast.error(`${error}`);
      return null;
    });

    if (data) {
      modelId = data.model_id ?? '';
      toast.success('Configuración guardada');
    }
    saving = false;
  };

  onMount(fetchConfig);
</script>

<div class="space-y-4">
  <div class="flex items-start justify-between gap-3">
    <div class="space-y-1">
      <div class="text-sm font-medium">Tareas programadas</div>
      <div class="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
        Define el ID de modelo que usará el worker de tareas programadas al llamar a Soren. Deja vacío para usar el
        valor por defecto.
      </div>
    </div>
  </div>

  <div class="space-y-1">
    <label class="text-xs uppercase tracking-wide text-gray-500">Model ID</label>
    <input
      class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm"
      placeholder="p. ej. soren o openrouter/anthropic/claude-3.5-sonnet"
      bind:value={modelId}
      autocomplete="off"
      disabled={loading}
    />
    <div class="text-[0.7rem] text-gray-500">Se persiste en la configuración global.</div>
  </div>

  <div class="flex items-center space-x-2">
    <button
      type="button"
      class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-60"
      on:click={handleSave}
      disabled={loading || saving}
    >
      {saving ? 'Guardando...' : 'Guardar modelo'}
    </button>
    <button
      type="button"
      class="px-3.5 py-1.5 text-sm font-medium hover:bg-black/5 dark:hover:bg-white/5 rounded-full disabled:opacity-60"
      on:click={fetchConfig}
      disabled={loading || saving}
    >
      Recargar
    </button>
  </div>
</div>
