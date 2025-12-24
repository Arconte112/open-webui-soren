<script lang="ts">
import { config, mobile, settings, showCallOverlay, TTSWorker } from '$lib/stores';
import { onMount, tick, onDestroy, createEventDispatcher } from 'svelte';

const dispatch = createEventDispatcher();

import { blobToFile } from '$lib/utils';
import { generateEmoji } from '$lib/apis';
import { synthesizeOpenAISpeech, transcribeAudio } from '$lib/apis/audio';

import { toast } from 'svelte-sonner';
import VideoInputMenu from './CallOverlay/VideoInputMenu.svelte';
import { KokoroWorker } from '$lib/workers/KokoroWorker';
import HalVisualizer, { type Emotion, type VisualState } from './CallOverlay/HalVisualizer.svelte';
import { MicVAD, utils as vadUtils } from '@ricky0123/vad-web';

const onnxRuntimeVersion = ONNXRUNTIME_WEB_VERSION;
const ONNX_WASM_BASE = `https://cdn.jsdelivr.net/npm/onnxruntime-web@${onnxRuntimeVersion}/dist/`;

	export let eventTarget: EventTarget;
	export let submitPrompt: Function;
	export let stopResponse: Function;
	export let files;
	export let chatId;
	export let modelId;
	export let fullscreen = false;

	let wakeLock = null;

	let loading = false;
	let confirmed = false;
	let interrupted = false;
	let assistantSpeaking = false;
	let visualState: VisualState = 'idle';
	let emotion: Emotion = 'neutral';
	const messageEmotions = new Map<string, Emotion>();
	const emotionRegex = /^\s*\[E:(neutral|happy|sad|excited)\]\s*/i;
	const retroButtonClass =
		'border border-[#ff3333] text-[#ff3333] font-mono text-[11px] tracking-[0.18em] uppercase px-3 py-2 leading-none rounded-sm bg-[rgba(0,0,0,0.6)] transition-all duration-150 hover:shadow-[0_0_12px_rgba(255,51,51,0.35)] hover:border-[#ff4d4d] hover:bg-[rgba(20,0,0,0.7)] active:opacity-80 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#ff3333]';

	let emoji = null;
	let camera = false;
	let cameraStream = null;

	let chatStreaming = false;
	let rmsLevel = 0;
	let hasStartedSpeaking = false;
	let mediaRecorder;
	let audioStream = null;
	let audioChunks = [];
	let micMuted = false;
	let ttsPlaying = false;
	let ttsLevel = 0;
	let ttsAudioContext: AudioContext | null = null;
	let ttsAnalyser: AnalyserNode | null = null;
	let ttsSource: MediaElementAudioSourceNode | null = null;
	let ttsDataArray: Uint8Array | null = null;
	let ttsLevelRaf: number | null = null;
	let pushBackSignal = 0;
	let clickTimeout: ReturnType<typeof setTimeout> | null = null;

	// VAD (Voice Activity Detection) state
	let vadInstance: MicVAD | null = null;
	let vadReady = false;
	let useVADFallback = false; // If true, use legacy threshold-based detection

	const handleCoreClick = () => {
		if (clickTimeout) {
			clearTimeout(clickTimeout);
			clickTimeout = null;
		}

		clickTimeout = setTimeout(() => {
			if (visualState === 'responding') {
				pushBackSignal += 1;
				stopAllAudio();
			}
			clickTimeout = null;
		}, 220);
	};

	const handleCoreDoubleClick = async () => {
		if (clickTimeout) {
			clearTimeout(clickTimeout);
			clickTimeout = null;
		}
		await toggleMicMute();
	};

	// visualState should be 'thinking' from when user finishes speaking until TTS starts playing
	// 'responding' only when TTS is actually playing audio
	$: visualState = (loading || (chatStreaming && !assistantSpeaking))
		? 'thinking'
		: assistantSpeaking
			? 'responding'
			: hasStartedSpeaking
				? 'userSpeaking'
				: 'idle';

	let videoInputDevices = [];
	let selectedVideoInputDeviceId = null;

	const getVideoInputDevices = async () => {
		const devices = await navigator.mediaDevices.enumerateDevices();
		videoInputDevices = devices.filter((device) => device.kind === 'videoinput');

		if (!!navigator.mediaDevices.getDisplayMedia) {
			videoInputDevices = [
				...videoInputDevices,
				{
					deviceId: 'screen',
					label: 'Screen Share'
				}
			];
		}

		console.log(videoInputDevices);
		if (selectedVideoInputDeviceId === null && videoInputDevices.length > 0) {
			selectedVideoInputDeviceId = videoInputDevices[0].deviceId;
		}
	};

	const startCamera = async () => {
		await getVideoInputDevices();

		if (cameraStream === null) {
			camera = true;
			await tick();
			try {
				await startVideoStream();
			} catch (err) {
				console.error('Error accessing webcam: ', err);
			}
		}
	};

	const startVideoStream = async () => {
		const video = document.getElementById('camera-feed');
		if (video) {
			if (selectedVideoInputDeviceId === 'screen') {
				cameraStream = await navigator.mediaDevices.getDisplayMedia({
					video: {
						cursor: 'always'
					},
					audio: false
				});
			} else {
				cameraStream = await navigator.mediaDevices.getUserMedia({
					video: {
						deviceId: selectedVideoInputDeviceId ? { exact: selectedVideoInputDeviceId } : undefined
					}
				});
			}

			if (cameraStream) {
				await getVideoInputDevices();
				video.srcObject = cameraStream;
				await video.play();
			}
		}
	};

	const stopVideoStream = async () => {
		if (cameraStream) {
			const tracks = cameraStream.getTracks();
			tracks.forEach((track) => track.stop());
		}

		cameraStream = null;
	};

	const takeScreenshot = () => {
		const video = document.getElementById('camera-feed');
		const canvas = document.getElementById('camera-canvas');

		if (!canvas) {
			return;
		}

		const context = canvas.getContext('2d');

		// Make the canvas match the video dimensions
		canvas.width = video.videoWidth;
		canvas.height = video.videoHeight;

		// Draw the image from the video onto the canvas
		context.drawImage(video, 0, 0, video.videoWidth, video.videoHeight);

		// Convert the canvas to a data base64 URL and console log it
		const dataURL = canvas.toDataURL('image/png');
		console.log(dataURL);

		return dataURL;
	};

	const stopCamera = async () => {
		await stopVideoStream();
		camera = false;
	};

	const resetEmotion = () => {
		emotion = 'neutral';
	};

	const stripEmotionCode = (content: string, messageId: string) => {
		const match = emotionRegex.exec(content);
		if (match) {
			const detected = match[1].toLowerCase() as Emotion;
			emotion = detected;
			messageEmotions.set(messageId, detected);
			return content.replace(emotionRegex, '').trimStart();
		}

		if (messageEmotions.has(messageId)) {
			emotion = messageEmotions.get(messageId) as Emotion;
		}

		return content;
	};

	const getEmojiFontSize = (large = false) => {
		const base = large ? 9 : 4;
		const boost = Math.min(3, rmsLevel * 12);
		return `${base + boost}rem`;
	};

	// Legacy threshold for fallback detection (only used if VAD fails to load)
	const MIN_DECIBELS = -55;

	// Reactive muting: when chatStreaming or assistantSpeaking, mute the mic track (not just analysis)
	$: {
		if (audioStream && !($settings?.voiceInterruption ?? false)) {
			const shouldMute = chatStreaming || assistantSpeaking;
			audioStream.getAudioTracks().forEach((track) => {
				track.enabled = !shouldMute;
			});
			// Also pause/resume VAD if available
			if (vadInstance && vadReady) {
				if (shouldMute) {
					vadInstance.pause();
				} else {
					vadInstance.start();
				}
			}
		}
	}

	const transcribeHandler = async (audioBlob) => {
		// Create a blob from the audio chunks

		await tick();
		const file = blobToFile(audioBlob, 'recording.wav');

		const res = await transcribeAudio(
			localStorage.token,
			file,
			$settings?.audio?.stt?.language
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			console.log(res.text);

			if (res.text !== '') {
				const _responses = await submitPrompt(res.text, { _raw: true });
				console.log(_responses);
			}
		}
	};

	const stopRecordingCallback = async (_continue = true) => {
		if ($showCallOverlay) {
			console.log('%c%s', 'color: red; font-size: 20px;', '🚨 stopRecordingCallback 🚨');

			// For VAD mode, audioChunks are handled in onSpeechEnd callback
			// This function is mainly for cleanup when overlay closes

			audioChunks = [];
			mediaRecorder = false;

			if (!_continue) {
				// Cleanup VAD
				if (vadInstance) {
					vadInstance.pause();
					vadInstance.destroy();
					vadInstance = null;
					vadReady = false;
				}
			}
		} else {
			audioChunks = [];
			mediaRecorder = false;

			// Cleanup VAD
			if (vadInstance) {
				vadInstance.pause();
				vadInstance.destroy();
				vadInstance = null;
				vadReady = false;
			}

			if (audioStream) {
				const tracks = audioStream.getTracks();
				tracks.forEach((track) => track.stop());
			}
			audioStream = null;
			micMuted = false;
		}
	};

	// Initialize VAD with Silero model
	const initVAD = async () => {
		try {
			console.log('🎤 Initializing VAD with Silero model...');

			vadInstance = await MicVAD.new({
				// Use v5 model explicitly (default is "legacy" which looks for silero_vad_legacy.onnx)
				model: 'v5',
				// Use CDN for ONNX WASM runtime (pinned to the installed onnxruntime-web version to avoid 404s)
				onnxWASMBasePath: ONNX_WASM_BASE,
				// Use CDN for VAD model and worklet files (must match installed version 0.0.30)
				baseAssetPath: 'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/dist/',

				// Speech detection callbacks
				onSpeechStart: () => {
					if (!$showCallOverlay) return;
					// Don't process if we're in responding/thinking state (unless voice interruption enabled)
					if ((chatStreaming || assistantSpeaking) && !($settings?.voiceInterruption ?? false)) {
						return;
					}

					console.log('%c%s', 'color: green; font-size: 20px;', '🎤 VAD: Speech started');
					hasStartedSpeaking = true;
					stopAllAudio(); // Interrupt any playing TTS
				},

				onSpeechEnd: async (audio: Float32Array) => {
					if (!$showCallOverlay) return;
					// Don't process if we're in responding/thinking state
					if ((chatStreaming || assistantSpeaking) && !($settings?.voiceInterruption ?? false)) {
						hasStartedSpeaking = false;
						return;
					}

					console.log('%c%s', 'color: blue; font-size: 20px;', '🎤 VAD: Speech ended');

					// Convert Float32Array to WAV blob
					const wavBuffer = vadUtils.encodeWAV(audio);
					const audioBlob = new Blob([wavBuffer], { type: 'audio/wav' });

					loading = true;
					emoji = null;

					// Take screenshot if camera is active
					if (cameraStream) {
						const imageUrl = takeScreenshot();
						files = [{ type: 'image', url: imageUrl }];
					}

					// Send to transcription
					await transcribeHandler(audioBlob);

					hasStartedSpeaking = false;
					loading = false;
				},

				// Real-time audio frame callback for visualization
				onFrameProcessed: (probs: { isSpeech: number; notSpeech: number }) => {
					// Use speech probability for RMS visualization
					rmsLevel = probs.isSpeech * 0.5;
				},

				// VAD parameters
				positiveSpeechThreshold: 0.5,
				negativeSpeechThreshold: 0.35,
				redemptionFrames: 8,
				minSpeechFrames: 3,
				preSpeechPadFrames: 1,
			});

			vadReady = true;
			console.log('%c%s', 'color: green; font-size: 24px;', '✅ VAD initialized successfully with Silero model');

			// Start VAD
			vadInstance.start();

		} catch (error) {
			console.warn('⚠️ VAD initialization failed, using fallback detection:', error);
			useVADFallback = true;
			vadReady = false;
			// Fall back to legacy detection
			startLegacyRecording();
		}
	};

	// Legacy recording with threshold-based detection (fallback)
	const startLegacyRecording = async () => {
		if ($showCallOverlay) {
			if (!audioStream) {
				audioStream = await navigator.mediaDevices.getUserMedia({
					audio: {
						echoCancellation: true,
						noiseSuppression: true,
						autoGainControl: true
					}
				});
			}
			mediaRecorder = new MediaRecorder(audioStream);

			mediaRecorder.onstart = () => {
				console.log('Recording started (legacy mode)');
				audioChunks = [];
			};

			mediaRecorder.ondataavailable = (event) => {
				if (hasStartedSpeaking) {
					audioChunks.push(event.data);
				}
			};

			mediaRecorder.onstop = async (e) => {
				console.log('Recording stopped (legacy mode)', audioStream, e);

				if ($showCallOverlay && confirmed) {
					const _audioChunks = audioChunks.slice(0);
					audioChunks = [];

					loading = true;
					emoji = null;

					if (cameraStream) {
						const imageUrl = takeScreenshot();
						files = [{ type: 'image', url: imageUrl }];
					}

					const audioBlob = new Blob(_audioChunks, { type: 'audio/wav' });
					await transcribeHandler(audioBlob);

					confirmed = false;
					loading = false;
					hasStartedSpeaking = false;

					// Continue recording
					startLegacyRecording();
				}
			};

			analyseAudio(audioStream);
		}
	};

	const startRecording = async () => {
		if ($showCallOverlay) {
			// Try to initialize VAD, fall back to legacy if it fails
			if (!useVADFallback && !vadInstance) {
				await initVAD();
			} else if (useVADFallback) {
				await startLegacyRecording();
			}
		}
	};

	const stopAudioStream = async () => {
		// Stop VAD first
		if (vadInstance) {
			try {
				vadInstance.pause();
				vadInstance.destroy();
			} catch (e) {
				console.log('Error destroying VAD:', e);
			}
			vadInstance = null;
			vadReady = false;
		}

		try {
			if (mediaRecorder) {
				mediaRecorder.stop();
			}
		} catch (error) {
			console.log('Error stopping audio stream:', error);
		}

		if (!audioStream) return;

		audioStream.getAudioTracks().forEach(function (track) {
			track.stop();
		});

		audioStream = null;
		micMuted = false;
	};

	const setMicEnabled = (enabled: boolean) => {
		if (!audioStream) return;
		audioStream.getAudioTracks().forEach((track) => {
			track.enabled = enabled;
		});
		micMuted = !enabled;
	};

	const toggleMicMute = async () => {
		if (!audioStream) {
			audioStream = await navigator.mediaDevices.getUserMedia({
				audio: {
					echoCancellation: true,
					noiseSuppression: true,
					autoGainControl: true
				}
			});
		}

		setMicEnabled(micMuted); // flips current state
	};

	const smoothLerp = (from: number, to: number, alpha: number) => from + (to - from) * alpha;

	// Track which audio element has been connected to avoid duplicate connections
	let connectedAudioElement: HTMLAudioElement | null = null;

	const ensureTTSAnalyser = async () => {
		const audioElement = document.getElementById('audioElement') as HTMLAudioElement | null;
		if (!audioElement) return null;

		if (!ttsAudioContext) {
			ttsAudioContext = new AudioContext();
		}

		// Only create MediaElementSource if this element hasn't been connected before
		// or if it's a different element
		if (!ttsSource || connectedAudioElement !== audioElement) {
			// If we had a previous source connected to a different element, we can't reuse it
			// But we also can't reconnect the same element - check if it's the same one
			if (connectedAudioElement === audioElement && ttsSource) {
				// Same element already connected, just resume context
				await ttsAudioContext.resume();
				return ttsAnalyser;
			}

			try {
				ttsSource = ttsAudioContext.createMediaElementSource(audioElement);
				connectedAudioElement = audioElement;
				ttsAnalyser = ttsAudioContext.createAnalyser();
				ttsAnalyser.fftSize = 2048;
				ttsAnalyser.smoothingTimeConstant = 0.82;
				ttsSource.connect(ttsAnalyser);
				ttsAnalyser.connect(ttsAudioContext.destination);
				ttsDataArray = new Uint8Array(ttsAnalyser.fftSize);
			} catch (e) {
				// Element may already be connected from a previous session
				console.warn('AudioElement already connected, reusing existing analyser');
			}
		}

		await ttsAudioContext.resume();
		return ttsAnalyser;
	};

	const stopTTSMonitoring = () => {
		ttsPlaying = false;
		if (ttsLevelRaf) {
			cancelAnimationFrame(ttsLevelRaf);
			ttsLevelRaf = null;
		}
		ttsLevel = 0;
	};

	const updateTTSLevel = () => {
		if (!ttsPlaying || !ttsAnalyser || !ttsDataArray) {
			ttsLevel = smoothLerp(ttsLevel, 0, 0.18);
		} else {
			ttsAnalyser.getByteTimeDomainData(ttsDataArray);
			const rms = calculateRMS(ttsDataArray);
			const boosted = Math.max(0, rms - 0.02) * 3; // lift quiet parts
			ttsLevel = smoothLerp(ttsLevel, Math.min(1.6, boosted), 0.22);
		}

		ttsLevelRaf = requestAnimationFrame(updateTTSLevel);
	};

	// Function to calculate the RMS level from time domain data
	const calculateRMS = (data: Uint8Array) => {
		let sumSquares = 0;
		for (let i = 0; i < data.length; i++) {
			const normalizedValue = (data[i] - 128) / 128; // Normalize the data
			sumSquares += normalizedValue * normalizedValue;
		}
		return Math.sqrt(sumSquares / data.length);
	};

	// Legacy audio analysis - only used as fallback when VAD fails to load
	const analyseAudio = (stream) => {
		if (!useVADFallback) {
			console.log('analyseAudio called but VAD is active, skipping legacy detection');
			return;
		}

		const audioContext = new AudioContext();
		const audioStreamSource = audioContext.createMediaStreamSource(stream);

		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = MIN_DECIBELS;
		audioStreamSource.connect(analyser);

		const bufferLength = analyser.frequencyBinCount;

		const domainData = new Uint8Array(bufferLength);
		const timeDomainData = new Uint8Array(analyser.fftSize);

		let lastSoundTime = Date.now();
		hasStartedSpeaking = false;

		console.log('🔊 Sound detection started (LEGACY FALLBACK MODE)', lastSoundTime, hasStartedSpeaking);

		const detectSound = () => {
			const processFrame = () => {
				if (!mediaRecorder || !$showCallOverlay) {
					return;
				}

				// Ignore microphone input during text generation (chatStreaming) and TTS playback (assistantSpeaking)
				// unless voice interruption is explicitly enabled
				if ((chatStreaming || assistantSpeaking) && !($settings?.voiceInterruption ?? false)) {
					// Mute the audio analysis during response generation and TTS playback
					analyser.maxDecibels = 0;
					analyser.minDecibels = -1;
				} else {
					analyser.minDecibels = MIN_DECIBELS;
					analyser.maxDecibels = -30;
				}

				analyser.getByteTimeDomainData(timeDomainData);
				analyser.getByteFrequencyData(domainData);

				// Calculate RMS level from time domain data
				rmsLevel = calculateRMS(timeDomainData);

				// Check if initial speech/noise has started
				const hasSound = domainData.some((value) => value > 0);
				if (hasSound) {
					// BIG RED TEXT
					console.log('%c%s', 'color: red; font-size: 20px;', '🔊 Sound detected (legacy)');
					if (mediaRecorder && mediaRecorder.state !== 'recording') {
						mediaRecorder.start();
					}

					if (!hasStartedSpeaking) {
						hasStartedSpeaking = true;
						stopAllAudio();
					}

					lastSoundTime = Date.now();
				}

				// Start silence detection only after initial speech/noise has been detected
				if (hasStartedSpeaking) {
					if (Date.now() - lastSoundTime > 2000) {
						confirmed = true;

						if (mediaRecorder) {
							console.log('%c%s', 'color: red; font-size: 20px;', '🔇 Silence detected (legacy)');
							mediaRecorder.stop();
							return;
						}
					}
				}

				window.requestAnimationFrame(processFrame);
			};

			window.requestAnimationFrame(processFrame);
		};

		detectSound();
	};

	let finishedMessages = {};
	let currentMessageId = null;
	let currentUtterance = null;

	const speakSpeechSynthesisHandler = (content) => {
		if ($showCallOverlay) {
			return new Promise((resolve) => {
				let voices = [];
				const getVoicesLoop = setInterval(async () => {
					voices = await speechSynthesis.getVoices();
					if (voices.length > 0) {
						clearInterval(getVoicesLoop);

						const voice =
							voices
								?.filter(
									(v) => v.voiceURI === ($settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice)
								)
								?.at(0) ?? undefined;

						currentUtterance = new SpeechSynthesisUtterance(content);
						currentUtterance.rate = $settings.audio?.tts?.playbackRate ?? 1;

						if (voice) {
							currentUtterance.voice = voice;
						}

						assistantSpeaking = true;
						ttsPlaying = true;
						loading = false;
						speechSynthesis.speak(currentUtterance);
						currentUtterance.onend = async (e) => {
							assistantSpeaking = false;
							ttsPlaying = false;
							stopTTSMonitoring();
							await new Promise((r) => setTimeout(r, 200));
							resolve(e);
						};
					}
				}, 100);
			});
		} else {
			return Promise.resolve();
		}
	};

	const playAudio = (audio) => {
		if ($showCallOverlay) {
			return new Promise((resolve) => {
				const audioElement = document.getElementById('audioElement') as HTMLAudioElement;

				if (audioElement) {
					ensureTTSAnalyser();
					audioElement.src = audio.src;
					audioElement.muted = true;
					audioElement.playbackRate = $settings.audio?.tts?.playbackRate ?? 1;

					audioElement.onplaying = async () => {
						assistantSpeaking = true;
						ttsPlaying = true;
						loading = false;
						await ensureTTSAnalyser();
						if (!ttsLevelRaf) {
							updateTTSLevel();
						}
					};

					audioElement
						.play()
						.then(() => {
							audioElement.muted = false;
						})
						.catch((error) => {
							console.error(error);
						});

					audioElement.onended = async (e) => {
						ttsPlaying = false;
						assistantSpeaking = false;
						stopTTSMonitoring();
						await new Promise((r) => setTimeout(r, 100));
						resolve(e);
					};
				}
			});
		} else {
			return Promise.resolve();
		}
	};

	const stopAllAudio = async () => {
		assistantSpeaking = false;
		stopTTSMonitoring();
		interrupted = true;

		if (chatStreaming) {
			stopResponse();
		}

		if (currentUtterance) {
			speechSynthesis.cancel();
			currentUtterance = null;
		}

		const audioElement = document.getElementById('audioElement');
		if (audioElement) {
			audioElement.muted = true;
			audioElement.pause();
			audioElement.currentTime = 0;
		}
	};

	let audioAbortController = new AbortController();

	// Audio cache map where key is the content and value is the Audio object.
	const audioCache = new Map();
	const emojiCache = new Map();

	const fetchAudio = async (content) => {
		if (!audioCache.has(content)) {
			try {
				// Set the emoji for the content if needed
				if ($settings?.showEmojiInCall ?? false) {
					const emoji = await generateEmoji(localStorage.token, modelId, content, chatId);
					if (emoji) {
						emojiCache.set(content, emoji);
					}
				}

				if ($settings.audio?.tts?.engine === 'browser-kokoro') {
					const url = await $TTSWorker
						.generate({
							text: content,
							voice: $settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice
						})
						.catch((error) => {
							console.error(error);
							toast.error(`${error}`);
						});

					if (url) {
						audioCache.set(content, new Audio(url));
					}
				} else if ($config.audio.tts.engine !== '') {
					const res = await synthesizeOpenAISpeech(
						localStorage.token,
						$settings?.audio?.tts?.defaultVoice === $config.audio.tts.voice
							? ($settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice)
							: $config?.audio?.tts?.voice,
						content
					).catch((error) => {
						console.error(error);
						return null;
					});

					if (res) {
						const blob = await res.blob();
						const blobUrl = URL.createObjectURL(blob);
						audioCache.set(content, new Audio(blobUrl));
					}
				} else {
					audioCache.set(content, true);
				}
			} catch (error) {
				console.error('Error synthesizing speech:', error);
			}
		}

		return audioCache.get(content);
	};

	let messages = {};

	const monitorAndPlayAudio = async (id, signal) => {
		while (!signal.aborted) {
			if (messages[id] && messages[id].length > 0) {
				// Retrieve the next content string from the queue
				const content = messages[id].shift(); // Dequeues the content for playing

				if (audioCache.has(content)) {
					// If content is available in the cache, play it

					// Set the emoji for the content if available
					if (($settings?.showEmojiInCall ?? false) && emojiCache.has(content)) {
						emoji = emojiCache.get(content);
					} else {
						emoji = null;
					}

					if ($config.audio.tts.engine !== '') {
						try {
							console.log(
								'%c%s',
								'color: red; font-size: 20px;',
								`Playing audio for content: ${content}`
							);

							const audio = audioCache.get(content);
							await playAudio(audio); // Here ensure that playAudio is indeed correct method to execute
							console.log(`Played audio for content: ${content}`);
							await new Promise((resolve) => setTimeout(resolve, 200)); // Wait before retrying to reduce tight loop
						} catch (error) {
							console.error('Error playing audio:', error);
						}
					} else {
						await speakSpeechSynthesisHandler(content);
					}
				} else {
					// If not available in the cache, push it back to the queue and delay
					messages[id].unshift(content); // Re-queue the content at the start
					console.log(`Audio for "${content}" not yet available in the cache, re-queued...`);
					await new Promise((resolve) => setTimeout(resolve, 200)); // Wait before retrying to reduce tight loop
				}
			} else if (finishedMessages[id] && messages[id] && messages[id].length === 0) {
				// If the message is finished and there are no more messages to process, break the loop
				assistantSpeaking = false;
				ttsPlaying = false;
				stopTTSMonitoring();
				break;
			} else {
				// No messages to process, sleep for a bit
				await new Promise((resolve) => setTimeout(resolve, 200));
			}
		}
		console.log(`Audio monitoring and playing stopped for message ID ${id}`);
	};

	const chatStartHandler = async (e) => {
		const { id } = e.detail;

		chatStreaming = true;
		loading = true;

		if (currentMessageId !== id) {
			console.log(`Received chat start event for message ID ${id}`);

			resetEmotion();
			messageEmotions.delete(id);

			currentMessageId = id;
			if (audioAbortController) {
				audioAbortController.abort();
			}
			audioAbortController = new AbortController();

			// Start monitoring and playing audio for the message ID
			monitorAndPlayAudio(id, audioAbortController.signal);
		}
	};

	const chatEventHandler = async (e) => {
		const { id, content } = e.detail;
		// "id" here is message id
		// if "id" is not the same as "currentMessageId" then do not process
		// "content" here is a sentence from the assistant,
		// there will be many sentences for the same "id"

		if (currentMessageId === id) {
			console.log(`Received chat event for message ID ${id}: ${content}`);
			let processedContent = stripEmotionCode(content, id);
			if (processedContent.trim() === '') {
				return;
			}

			try {
				if (messages[id] === undefined) {
					messages[id] = [processedContent];
				} else {
					messages[id].push(processedContent);
				}

				console.log(processedContent);

				fetchAudio(processedContent);
			} catch (error) {
				console.error('Failed to fetch or play audio:', error);
			}
		}
	};

	const chatFinishHandler = async (e) => {
		const { id } = e.detail;
		finishedMessages[id] = true;

		chatStreaming = false;
	};

	onMount(async () => {
		const setWakeLock = async () => {
			try {
				wakeLock = await navigator.wakeLock.request('screen');
			} catch (err) {
				// The Wake Lock request has failed - usually system related, such as battery.
				console.log(err);
			}

			if (wakeLock) {
				// Add a listener to release the wake lock when the page is unloaded
				wakeLock.addEventListener('release', () => {
					// the wake lock has been released
					console.log('Wake Lock released');
				});
			}
		};

		if ('wakeLock' in navigator) {
			await setWakeLock();

			document.addEventListener('visibilitychange', async () => {
				// Re-request the wake lock if the document becomes visible
				if (wakeLock !== null && document.visibilityState === 'visible') {
					await setWakeLock();
				}
			});
		}

		startRecording();

		eventTarget.addEventListener('chat:start', chatStartHandler);
		eventTarget.addEventListener('chat', chatEventHandler);
		eventTarget.addEventListener('chat:finish', chatFinishHandler);

		return async () => {
			await stopAllAudio();

			stopAudioStream();

			eventTarget.removeEventListener('chat:start', chatStartHandler);
			eventTarget.removeEventListener('chat', chatEventHandler);
			eventTarget.removeEventListener('chat:finish', chatFinishHandler);

			audioAbortController.abort();
			await tick();

			await stopAllAudio();
			resetEmotion();
			messageEmotions.clear();

			await stopRecordingCallback(false);
			await stopCamera();
		};
	});

	onDestroy(async () => {
		if (clickTimeout) {
			clearTimeout(clickTimeout);
			clickTimeout = null;
		}

		// Clean up VAD instance
		if (vadInstance) {
			try {
				vadInstance.pause();
				vadInstance.destroy();
			} catch (e) {
				console.log('Error destroying VAD on destroy:', e);
			}
			vadInstance = null;
			vadReady = false;
		}

		await stopAllAudio();
		await stopRecordingCallback(false);
		await stopCamera();
		resetEmotion();
		messageEmotions.clear();

		await stopAudioStream();
		eventTarget.removeEventListener('chat:start', chatStartHandler);
		eventTarget.removeEventListener('chat', chatEventHandler);
		eventTarget.removeEventListener('chat:finish', chatFinishHandler);
		audioAbortController.abort();

		await tick();

		await stopAllAudio();
	});
</script>

{#if $showCallOverlay}
	<div
		class={`${fullscreen
			? 'fixed inset-0 w-screen h-[100dvh] max-w-none max-h-none z-50'
			: 'relative w-full max-w-[90vw] h-full max-h-[100dvh]'} overflow-hidden bg-black text-[#ff3333]`}
	>
		{#if camera}
			<div class="relative h-full w-full">
				<!-- svelte-ignore a11y-media-has-caption -->
				<video
					id="camera-feed"
					autoplay
					class="absolute inset-0 h-full w-full object-cover object-center"
					playsinline
				/>

				<canvas id="camera-canvas" style="display:none;" />

				<div class="pointer-events-none absolute bottom-4 right-4 w-24 h-24 md:w-28 md:h-28">
					<div
						class="relative w-full h-full opacity-90 pointer-events-auto"
						on:click={handleCoreClick}
						on:dblclick|preventDefault={handleCoreDoubleClick}
					>
						<HalVisualizer
							{emotion}
							state={visualState}
							{rmsLevel}
							muted={micMuted}
							responseLevel={ttsLevel}
							{pushBackSignal}
							isMobile={$mobile}
						/>

						{#if emoji}
							<div
								class="absolute inset-0 flex items-center justify-center text-white drop-shadow-xl"
								style={`font-size:${getEmojiFontSize(false)};`}
							>
								{emoji}
							</div>
						{/if}
					</div>
				</div>
			</div>
		{:else}
			<div
				role="button"
				tabindex="0"
				class="relative h-full w-full outline-none select-none"
				on:click={handleCoreClick}
				on:dblclick|preventDefault={handleCoreDoubleClick}
				on:keydown={(event) => {
					if (event.key === 'Enter' || event.key === ' ') {
						event.preventDefault();
						if (visualState === 'responding') {
							stopAllAudio();
						}
					}
				}}
			>
				<HalVisualizer
					{emotion}
					state={visualState}
					{rmsLevel}
					muted={micMuted}
					responseLevel={ttsLevel}
					{pushBackSignal}
					isMobile={$mobile}
				/>

				{#if emoji}
					<div
						class="absolute inset-0 flex items-center justify-center text-white drop-shadow-2xl"
						style={`font-size:${getEmojiFontSize(true)};`}
					>
						{emoji}
					</div>
				{/if}
			</div>
		{/if}

		<div class="pointer-events-none absolute inset-x-3 bottom-3 md:inset-x-4 md:bottom-4">
			<div class="flex items-end justify-between gap-3">
				<div class="flex gap-2 pointer-events-auto">
					{#if camera}
						<VideoInputMenu
							devices={videoInputDevices}
							on:change={async (e) => {
								console.log(e.detail);
								selectedVideoInputDeviceId = e.detail;
								await stopVideoStream();
								await startVideoStream();
							}}
						>
							<button class={retroButtonClass} type="button" aria-label="Video source">
								<span class="hidden md:inline">INPUT</span>
								<span class="md:ml-1">CAM</span>
							</button>
						</VideoInputMenu>
						<button type="button" class={retroButtonClass} on:click={stopCamera} aria-pressed="true">
							CAM OFF
						</button>
					{:else}
						<button
							type="button"
							class={retroButtonClass}
							on:click={async () => {
								await navigator.mediaDevices.getUserMedia({ video: true });
								startCamera();
							}}
						>
							CAM/SHARE
						</button>
					{/if}
				</div>

				<div class="flex gap-2 pointer-events-auto">
					<button
						class={retroButtonClass}
						on:click={async () => {
							await stopAudioStream();
							await stopCamera();

							showCallOverlay.set(false);
							dispatch('close');
						}}
						type="button"
					>
						END
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}
