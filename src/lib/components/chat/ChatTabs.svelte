<script lang="ts">
	import { getContext } from 'svelte';
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';

	import { activeChatTabId, chatTabs } from '$lib/stores';

	import Tooltip from '../common/Tooltip.svelte';
	import Plus from '../icons/Plus.svelte';
	import XMark from '../icons/XMark.svelte';

	const i18n = getContext('i18n');

	const NEW_CHAT_TAB_ID = 'new';
	const DEFAULT_MODEL_FLAG = 'chat-tabs-use-default-models';

	const getFallbackTitle = (title: string | null | undefined) => {
		const trimmed = (title ?? '').trim();
		return trimmed.length > 0 ? trimmed : $i18n.t('New Chat');
	};

	const navigateToTab = async (tabId: string) => {
		activeChatTabId.set(tabId);

		if (tabId === NEW_CHAT_TAB_ID) {
			await goto('/');
		} else {
			await goto(`/c/${tabId}`);
		}
	};

	const closeTab = async (tabId: string) => {
		const currentTabs = get(chatTabs);
		const currentActiveId = get(activeChatTabId);
		const closingIdx = currentTabs.findIndex((tab) => tab.id === tabId);
		const remainingTabs = currentTabs.filter((tab) => tab.id !== tabId);

		chatTabs.set(remainingTabs);

		if (tabId !== currentActiveId) {
			return;
		}

		const nextTab =
			remainingTabs[closingIdx - 1] ?? remainingTabs[closingIdx] ?? remainingTabs.at(0);

		if (nextTab) {
			await navigateToTab(nextTab.id);
		} else {
			await navigateToTab(NEW_CHAT_TAB_ID);
		}
	};

	const createNewTab = async () => {
		try {
			sessionStorage.setItem(DEFAULT_MODEL_FLAG, 'true');
		} catch {}
		await navigateToTab(NEW_CHAT_TAB_ID);
	};
</script>

<div class="tabs-row">
	<div class="tabs" role="tablist" aria-label={$i18n.t('Chat tabs')}>
		{#each $chatTabs as tab (tab.id)}
			<div
				role="tab"
				tabindex="0"
				aria-selected={tab.id === $activeChatTabId}
				class="tab {tab.id === $activeChatTabId ? 'active' : ''}"
				on:click={() => navigateToTab(tab.id)}
				on:keydown={(e) => {
					if (e.key === 'Enter' || e.key === ' ') {
						e.preventDefault();
						navigateToTab(tab.id);
					}
				}}
			>
				<span class="tab-title" title={getFallbackTitle(tab.title)}>
					{getFallbackTitle(tab.title)}
				</span>
				<button
					class="tab-close"
					on:click|stopPropagation={() => closeTab(tab.id)}
					aria-label={$i18n.t('Close tab')}
				>
					<XMark className="size-3" />
				</button>
			</div>
		{/each}

		<Tooltip content={$i18n.t('New Chat')}>
			<button class="tab-add" on:click={createNewTab} aria-label={$i18n.t('New Chat')}>
				<Plus className="size-4" />
			</button>
		</Tooltip>
	</div>
</div>

<style>
	.tabs-row {
		width: 100%;
		padding: 0 0.5rem;
	}

	.tabs {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		overflow-x: auto;
		padding: 0.25rem 0;
		scrollbar-width: none;
	}

	.tabs::-webkit-scrollbar {
		display: none;
	}

	.tab {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		max-width: 14rem;
		padding: 0.3rem 0.55rem;
		border-radius: 0.65rem;
		font-size: 0.75rem;
		color: #6b7280;
		background: rgba(0, 0, 0, 0.04);
		transition: background 120ms ease, color 120ms ease;
		cursor: pointer;
		user-select: none;
		white-space: nowrap;
	}

	:global(.dark) .tab {
		background: rgba(255, 255, 255, 0.06);
		color: #9ca3af;
	}

	.tab.active {
		background: rgba(0, 0, 0, 0.08);
		color: #111827;
	}

	:global(.dark) .tab.active {
		background: rgba(255, 255, 255, 0.12);
		color: #f3f4f6;
	}

	.tab-title {
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.tab-close {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border-radius: 9999px;
		padding: 0.1rem;
		color: inherit;
		opacity: 0.6;
		transition: opacity 120ms ease, background 120ms ease;
	}

	.tab:hover .tab-close,
	.tab.active .tab-close {
		opacity: 1;
	}

	.tab-close:hover {
		background: rgba(0, 0, 0, 0.08);
	}

	:global(.dark) .tab-close:hover {
		background: rgba(255, 255, 255, 0.12);
	}

	.tab-add {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0.3rem;
		border-radius: 0.65rem;
		color: #6b7280;
		background: rgba(0, 0, 0, 0.04);
		transition: background 120ms ease, color 120ms ease;
	}

	:global(.dark) .tab-add {
		background: rgba(255, 255, 255, 0.06);
		color: #9ca3af;
	}

	.tab-add:hover {
		background: rgba(0, 0, 0, 0.08);
		color: #111827;
	}

	:global(.dark) .tab-add:hover {
		background: rgba(255, 255, 255, 0.12);
		color: #f3f4f6;
	}
</style>
