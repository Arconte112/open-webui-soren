<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import * as THREE from 'three';
  import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
  import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
  import { AfterimagePass } from 'three/examples/jsm/postprocessing/AfterimagePass.js';
  import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';

  export type VisualState = 'idle' | 'userSpeaking' | 'thinking' | 'responding';
  export type Emotion = 'neutral' | 'happy' | 'sad' | 'excited';

  export let state: VisualState = 'idle';
  export let emotion: Emotion = 'neutral';
  export let rmsLevel = 0;
  export let muted = false;
  export let pushBackSignal = 0;
  export let responseLevel = 0;
  export let isMobile = false;

  const CORE_COLORS: Record<Emotion, string> = {
    neutral: '#ff4444',
    happy: '#ffaa33',
    sad: '#4466ff',
    excited: '#ff2222'
  };

  const CLOUD_COLORS = {
    speaking: '#22d3ee',
    thinking: '#888888'
  } as const;

  const QUALITY_DESKTOP = {
    pixelRatioMax: 2,
    starCount: 2500,
    coreCount: 2200,
    cloudCount: 2200,
    enablePostFX: true,
    bloomStrength: 1.5,
    bloomRadius: 0.5
  };

  const QUALITY_MOBILE = {
    pixelRatioMax: 1.25,
    starCount: 900,
    coreCount: 900,
    cloudCount: 900,
    enablePostFX: false,
    bloomStrength: 1.0,
    bloomRadius: 0.35
  };

  let quality = QUALITY_DESKTOP;
  $: quality = isMobile ? QUALITY_MOBILE : QUALITY_DESKTOP;

  const vertexShaderSphere = `
    uniform float uTime;
    uniform vec3 uColorCore;
    uniform vec3 uColorCloud;
    uniform float uCorePulse;
    uniform float uCloudExpansion;
    uniform float uMuteProgress;
    uniform float uVoiceLevel;
    attribute float aRadius;
    attribute float aBaseSize;
    attribute float aBaseOpacity;
    attribute float aRandom;
    varying vec3 vColor;
    varying float vOpacity;

    float random(vec2 st) {
        return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123);
    }

    void main() {
      vec3 newPos = position;
      float sizeMultiplier = 1.0;

      if (aRadius < 0.3) {
          vColor = uColorCore;
          vOpacity = 1.0;
          sizeMultiplier = 1.0 + uCorePulse;
          float drift = sin(uTime * 0.5 + aRandom * 10.0) * 0.02;
          newPos += normalize(position) * drift;
      } else {
          vColor = uColorCloud;
          vOpacity = aBaseOpacity;

          float breath = 0.0;

          if (uCloudExpansion > 0.0) {
              float cycle = sin(uTime * 3.0) * 0.5 + 0.5;
              breath = uCloudExpansion * cycle * 0.2;
          } else if (uCloudExpansion < 0.0) {
              breath = uCloudExpansion * 0.2;
          } else {
              breath = sin(uTime * 1.5) * 0.03;
          }
          newPos += normalize(position) * breath;

          float noise = sin(uTime + aRandom * 10.0) * 0.02;
          newPos.x += noise;
          newPos.y += noise;
      }

      float darkness = 0.0;
      if (uMuteProgress > 0.001) {
          float effectRadius = uMuteProgress * 0.7;
          float innerEdge = effectRadius * 0.7;
          float outerEdge = effectRadius + 0.12;
          darkness = (1.0 - smoothstep(innerEdge, outerEdge, aRadius)) * uMuteProgress;
      }

      float dimFactor = 1.0 - darkness * 0.82;
      vColor = mix(vColor, vec3(0.02, 0.0, 0.0), clamp(darkness, 0.0, 1.0));
      vOpacity *= dimFactor;
      sizeMultiplier *= mix(1.0, 0.85, darkness);

      // Voice-driven breathing
      float voice = clamp(uVoiceLevel, 0.0, 2.0);
      if (aRadius < 0.3) {
          float voiceInflate = 1.0 + voice * 0.35;
          newPos *= voiceInflate;
          sizeMultiplier *= voiceInflate;
          vOpacity *= (1.0 + voice * 0.35);
      } else {
          float voiceBreath = 1.0 + voice * 0.12;
          newPos *= voiceBreath;
          sizeMultiplier *= voiceBreath;
      }

      vec4 mvPosition = modelViewMatrix * vec4(newPos, 1.0);
      gl_Position = projectionMatrix * mvPosition;
      gl_PointSize = aBaseSize * sizeMultiplier * (300.0 / -mvPosition.z);
    }
  `;

  const fragmentShaderSphere = `
    varying vec3 vColor;
    varying float vOpacity;

    void main() {
      vec2 uv = gl_PointCoord.xy - 0.5;
      float dist = length(uv);
      if (dist > 0.5) discard;
      float alpha = smoothstep(0.5, 0.3, dist) * vOpacity;
      gl_FragColor = vec4(vColor, alpha);
    }
  `;

  const vertexShaderStars = `
    uniform float uStarWarp;
    uniform float uStarPulse;
    attribute float aSize;
    attribute vec3 aColor;
    varying vec3 vColor;

    void main() {
      vColor = aColor;
      vec3 warped = position * (1.0 + uStarWarp);
      vec4 mvPosition = modelViewMatrix * vec4(warped, 1.0);
      gl_Position = projectionMatrix * mvPosition;
      gl_PointSize = aSize * (100.0 / -mvPosition.z) * (1.0 + uStarPulse);
    }
  `;

  const fragmentShaderStars = `
    uniform float uStarBrightness;
    varying vec3 vColor;

    void main() {
      vec2 uv = gl_PointCoord.xy - 0.5;
      float dist = length(uv);
      if (dist > 0.5) discard;
      float alpha = smoothstep(0.5, 0.1, dist);
      gl_FragColor = vec4(vColor * uStarBrightness, alpha);
    }
  `;

  export const EMOTION_COLORS: Record<Emotion, { core: THREE.Color; edge: THREE.Color }> = {
    neutral: {
      core: new THREE.Color('#ff0000'),
      edge: new THREE.Color('#330000')
    },
    happy: {
      core: new THREE.Color('#ff8800'),
      edge: new THREE.Color('#442200')
    },
    sad: {
      core: new THREE.Color('#0044ff'),
      edge: new THREE.Color('#001133')
    },
    excited: {
      core: new THREE.Color('#ff2222'),
      edge: new THREE.Color('#550000')
    }
  };

  let container: HTMLDivElement;
  let renderer: THREE.WebGLRenderer | null = null;
  let scene: THREE.Scene | null = null;
  let camera: THREE.PerspectiveCamera | null = null;
  let composer: EffectComposer | null = null;
  let afterimagePass: AfterimagePass | null = null;
  let bloomPass: UnrealBloomPass | null = null;
  let particlePoints: THREE.Points | null = null;
  let starsPoints: THREE.Points | null = null;
  let frameId: number | null = null;
  let resizeObserver: ResizeObserver | null = null;
  const clock = new THREE.Clock();

  const uniforms = {
    uTime: { value: 0 },
    uColorCore: { value: new THREE.Color(CORE_COLORS.neutral) },
    uColorCloud: { value: new THREE.Color(CORE_COLORS.neutral) },
    uCorePulse: { value: 0 },
    uCloudExpansion: { value: 0 },
    uMuteProgress: { value: 0 },
    uVoiceLevel: { value: 0 }
  } satisfies Record<string, { value: any }>;

  const currentValues = {
    rotY: 0.1,
    rotX: 0,
    corePulseIntensity: 0,
    corePulseSpeed: 1,
    cloudExpansion: 0,
    coreColor: new THREE.Color(CORE_COLORS.neutral),
    cloudColor: new THREE.Color(CORE_COLORS.neutral),
    cameraTick: 0,
    cameraSpeed: 0.005,
    cameraRadius: 4.5,
    muteProgress: 0,
    voiceLevel: 0,
    starBrightness: 0.6,
    starWarp: 0,
    starPulse: 0
  };

  const PUSH_DURATION = 0.7; // seconds
  const PUSH_DISTANCE = 3.0; // additional radius during push (more agresive)
  const PUSH_SHAKE = 0.08;
  let pushBackActive = false;
  let pushBackElapsed = 0;
  let lastPushBackSignal = pushBackSignal;

  let lastState: VisualState = 'idle';
  let starWaveTimer = 0;
  const STAR_WAVE_DURATION = 0.45;

  const createStarField = () => {
    if (!scene) return;
    const count = quality.starCount;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    const colorOptions = [
      new THREE.Color('#ffffff'),
      new THREE.Color('#eef2ff'),
      new THREE.Color('#ffffe0')
    ];

    for (let i = 0; i < count; i++) {
      const r = 40 + Math.random() * 60;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);

      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = r * Math.cos(phi);

      const c = colorOptions[Math.floor(Math.random() * colorOptions.length)];
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;

      sizes[i] = Math.random() * 0.5 + 0.2;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('aColor', new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.ShaderMaterial({
      vertexShader: vertexShaderStars,
      fragmentShader: fragmentShaderStars,
      uniforms: {
        uStarBrightness: { value: 0.6 },
        uStarWarp: { value: 0 },
        uStarPulse: { value: 0 }
      },
      transparent: true,
      depthWrite: false
    });

    starsPoints = new THREE.Points(geometry, material);
    scene.add(starsPoints);
  };

  const createParticleSphere = () => {
    if (!scene) return;

    const coreCount = quality.coreCount;
    const cloudCount = quality.cloudCount;
    const count = coreCount + cloudCount;
    const positions = new Float32Array(count * 3);
    const radius = new Float32Array(count);
    const sizes = new Float32Array(count);
    const opacities = new Float32Array(count);
    const randoms = new Float32Array(count);

    let idx = 0;
    for (let i = 0; i < coreCount; i++) {
      const u = Math.random();
      const v = Math.random();
      const theta = 2 * Math.PI * u;
      const phi = Math.acos(2 * v - 1);
      const r = Math.cbrt(Math.random()) * 0.28;

      positions[idx * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[idx * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[idx * 3 + 2] = r * Math.cos(phi);
      radius[idx] = r;
      sizes[idx] = 0.05 + Math.random() * 0.02;
      opacities[idx] = 1;
      randoms[idx] = Math.random();
      idx++;
    }

    for (let i = 0; i < cloudCount; i++) {
      const u = Math.random();
      const v = Math.random();
      const theta = 2 * Math.PI * u;
      const phi = Math.acos(2 * v - 1);
      const r = 0.35 + Math.cbrt(Math.random()) * 0.65;

      positions[idx * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[idx * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[idx * 3 + 2] = r * Math.cos(phi);
      radius[idx] = r;
      sizes[idx] = 0.015 + Math.random() * 0.01;
      opacities[idx] = 0.4 + Math.random() * 0.4;
      randoms[idx] = Math.random();
      idx++;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('aRadius', new THREE.BufferAttribute(radius, 1));
    geometry.setAttribute('aBaseSize', new THREE.BufferAttribute(sizes, 1));
    geometry.setAttribute('aBaseOpacity', new THREE.BufferAttribute(opacities, 1));
    geometry.setAttribute('aRandom', new THREE.BufferAttribute(randoms, 1));

    const material = new THREE.ShaderMaterial({
      uniforms,
      vertexShader: vertexShaderSphere,
      fragmentShader: fragmentShaderSphere,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });

    particlePoints = new THREE.Points(geometry, material);
    scene.add(particlePoints);
  };

  const resizeRenderer = () => {
    if (!container || !renderer || !camera || !composer) return;
    const { width, height } = container.getBoundingClientRect();
    if (width === 0 || height === 0) return;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
    composer.setSize(width, height);
    bloomPass?.setSize(width, height);
  };

  const initScene = () => {
    if (!container) return;

    scene = new THREE.Scene();
    scene.background = new THREE.Color('#000000');

    const { width, height } = container.getBoundingClientRect();
    camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 200);
    camera.position.set(0, 0, 4.5);

    renderer = new THREE.WebGLRenderer({
      antialias: !isMobile,
      alpha: false,
      powerPreference: isMobile ? 'low-power' : 'high-performance'
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, quality.pixelRatioMax));
    renderer.setSize(width, height);
    renderer.setClearColor('#000000', 1);

    container.appendChild(renderer.domElement);

    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    if (quality.enablePostFX) {
      afterimagePass = new AfterimagePass(0.85);
      composer.addPass(afterimagePass);

      bloomPass = new UnrealBloomPass(
        new THREE.Vector2(width, height),
        quality.bloomStrength,
        quality.bloomRadius,
        0.2
      );
      bloomPass.threshold = 0.2;
      bloomPass.strength = quality.bloomStrength;
      bloomPass.radius = quality.bloomRadius;
      composer.addPass(bloomPass);
    } else {
      afterimagePass = null;
      bloomPass = null;
    }

    createStarField();
    createParticleSphere();
    resizeRenderer();
  };

  const updateStateValues = (delta: number) => {
    if (!particlePoints) return;

    const targetCoreColor = new THREE.Color(CORE_COLORS[emotion]);
    const targetCloudColor = new THREE.Color(
      state === 'userSpeaking'
        ? CLOUD_COLORS.speaking
        : state === 'thinking'
          ? CLOUD_COLORS.thinking
          : CORE_COLORS[emotion]
    );

    let targetRotY = 0.1;
    let targetRotX = 0;
    let targetPulseIntensity = 0.05;
    let targetPulseSpeed = 1;
    let targetExpansion = 0;
    let targetVoiceLevel = 0;
    const respondingBase = { pulse: 0.18, speed: 6, expansion: 0.15 };

    switch (state) {
      case 'idle':
        targetExpansion = 0;
        break;
      case 'userSpeaking':
        targetRotY = 0.15;
        targetExpansion = 1;
        break;
      case 'thinking':
        targetRotY = 1.5;
        targetExpansion = -1.5;
        break;
      case 'responding':
        targetRotY = 0.2;
        targetVoiceLevel = Math.min(1.6, responseLevel * 3);
        targetPulseIntensity = respondingBase.pulse;
        targetPulseSpeed = respondingBase.speed;
        targetExpansion = respondingBase.expansion;
        break;
    }

    if (emotion === 'happy') {
      targetRotY *= 1.2;
      targetPulseSpeed *= 1.2;
    } else if (emotion === 'sad') {
      targetRotY *= 0.5;
      targetPulseSpeed *= 0.8;
    } else if (emotion === 'excited') {
      targetRotY *= 1.5;
      targetPulseIntensity += 0.05;
    }

    if (state === 'userSpeaking') {
      const rmsBoost = Math.min(rmsLevel * 10, 1.5);
      targetPulseIntensity += rmsBoost * 0.25;
      targetExpansion += rmsBoost * 0.6;
    }

    const lerpSpeed = delta * 2.5;
    currentValues.coreColor.lerp(targetCoreColor, lerpSpeed);
    currentValues.cloudColor.lerp(targetCloudColor, lerpSpeed);

    uniforms.uColorCore.value.copy(currentValues.coreColor);
    uniforms.uColorCloud.value.copy(currentValues.cloudColor);

    currentValues.rotY = THREE.MathUtils.lerp(currentValues.rotY, targetRotY, lerpSpeed);
    currentValues.rotX = THREE.MathUtils.lerp(currentValues.rotX, targetRotX, lerpSpeed);
    currentValues.corePulseIntensity = THREE.MathUtils.lerp(
      currentValues.corePulseIntensity,
      targetPulseIntensity,
      lerpSpeed
    );
    currentValues.corePulseSpeed = THREE.MathUtils.lerp(
      currentValues.corePulseSpeed,
      targetPulseSpeed,
      lerpSpeed
    );
    currentValues.cloudExpansion = THREE.MathUtils.lerp(
      currentValues.cloudExpansion,
      targetExpansion,
      lerpSpeed
    );
    currentValues.voiceLevel = THREE.MathUtils.lerp(currentValues.voiceLevel, targetVoiceLevel, delta * 8);

    if (state === 'responding') {
      const voice = currentValues.voiceLevel;
      targetPulseIntensity = respondingBase.pulse + voice * 0.55;
      targetPulseSpeed = respondingBase.speed + voice * 6;
      targetExpansion = respondingBase.expansion + voice * 0.35;
      currentValues.cloudExpansion = THREE.MathUtils.lerp(
        currentValues.cloudExpansion,
        targetExpansion,
        lerpSpeed
      );
    }

    if (state === 'responding') {
      const voice = currentValues.voiceLevel;
      targetPulseIntensity = respondingBase.pulse + voice * 0.55;
      targetPulseSpeed = respondingBase.speed + voice * 6;
      targetExpansion = respondingBase.expansion + voice * 0.35;
      currentValues.cloudExpansion = THREE.MathUtils.lerp(
        currentValues.cloudExpansion,
        targetExpansion,
        lerpSpeed
      );
    }

    const targetMute = muted ? 1 : 0;
    currentValues.muteProgress = THREE.MathUtils.lerp(
      currentValues.muteProgress,
      targetMute,
      delta * 3
    );

    const pulseSine = Math.sin(clock.elapsedTime * currentValues.corePulseSpeed);
    const dimmedPulse = pulseSine * currentValues.corePulseIntensity * (1 - currentValues.muteProgress * 0.85);
    uniforms.uCorePulse.value = dimmedPulse;
    uniforms.uCloudExpansion.value = currentValues.cloudExpansion;
    uniforms.uMuteProgress.value = currentValues.muteProgress;
    uniforms.uVoiceLevel.value = currentValues.voiceLevel;

    particlePoints.rotation.y += currentValues.rotY * delta;
    particlePoints.rotation.x += currentValues.rotX * delta;

    if (afterimagePass) {
      afterimagePass.damp = state === 'responding' ? 0.88 : 0.85;
    }
  };

  const updateCamera = (delta: number) => {
    if (!camera) return;
    const BASE_SPEED = 0.005;
    let targetSpeed = BASE_SPEED; // idle: velocidad base

    switch (state) {
      case 'userSpeaking':
        targetSpeed = BASE_SPEED * 0.25; // 20-30% del normal: casi estático
        break;
      case 'thinking':
        targetSpeed = BASE_SPEED * 0.9; // igual o un poco más lento
        break;
      case 'responding':
        targetSpeed = BASE_SPEED * 1.1; // drift activo pero sutil
        break;
    }

    if (emotion === 'excited') targetSpeed += 0.001; // ajuste fino
    if (emotion === 'sad') targetSpeed = Math.max(0.002, targetSpeed * 0.6);

    let targetRadius = 4.8;
    if (state === 'thinking') targetRadius = 5.6;
    if (state === 'userSpeaking') targetRadius = 4.9;
    if (state === 'responding') targetRadius = 4.8;

    targetRadius = THREE.MathUtils.clamp(targetRadius, 4.6, 6); // evita zoom excesivo

    if (pushBackActive) {
      pushBackElapsed += delta;
      const t = Math.min(pushBackElapsed / PUSH_DURATION, 1);
      const easeOut = 1 - Math.pow(1 - t, 1.6); // más brusco al inicio
      const offset = (1 - easeOut) * PUSH_DISTANCE;
      targetRadius += offset;
      if (pushBackElapsed >= PUSH_DURATION) {
        pushBackActive = false;
      }
    }

    targetRadius = THREE.MathUtils.clamp(targetRadius, 4.6, 8.5);

    const lerpFactor = delta * 1.5; // transiciones suaves entre estados
    currentValues.cameraSpeed = THREE.MathUtils.lerp(
      currentValues.cameraSpeed,
      targetSpeed,
      lerpFactor
    );
    currentValues.cameraRadius = THREE.MathUtils.lerp(
      currentValues.cameraRadius,
      targetRadius,
      lerpFactor
    );

    currentValues.cameraTick += currentValues.cameraSpeed;
    const t = currentValues.cameraTick;

    const xBase = Math.sin(t * 0.63) * (currentValues.cameraRadius * 0.6);
    const yBase = Math.sin(t * 0.84) * (currentValues.cameraRadius * 0.47);
    const zBase = Math.cos(t * 0.39) * currentValues.cameraRadius;

    let shake = 0;
    if (pushBackActive) {
      const intensity = (1 - Math.min(pushBackElapsed / PUSH_DURATION, 1));
      shake = Math.sin(clock.elapsedTime * 40) * PUSH_SHAKE * intensity;
    }

    const x = xBase + shake * 0.5;
    const y = yBase + shake * 0.3;
    const z = zBase + shake;

    camera.position.set(x, y, z);
    camera.lookAt(0, 0, 0);
  };

  const updateStars = (delta: number) => {
    if (!starsPoints) return;
    const mat = starsPoints.material as THREE.ShaderMaterial;
    let brightness = 0.55;
    let warp = 0.0;
    let pulse = 0.0;

    if (state !== lastState) {
      starWaveTimer = STAR_WAVE_DURATION;
      lastState = state;
    }

    switch (state) {
      case 'idle':
        brightness = 0.55;
        warp = 0;
        pulse = 0;
        break;
      case 'userSpeaking':
        brightness = 0.6;
        pulse = 0.03;
        warp = -0.01;
        break;
      case 'thinking':
        brightness = 0.65;
        warp = 0.08;
        pulse = 0.02;
        break;
      case 'responding':
        brightness = 0.6 + currentValues.voiceLevel * 0.2;
        warp = 0.03 + currentValues.voiceLevel * 0.05;
        pulse = 0.02 + currentValues.voiceLevel * 0.1;
        break;
    }

    // emotion adjustments
    if (emotion === 'happy') brightness += 0.1;
    if (emotion === 'sad') brightness = Math.max(0.35, brightness - 0.15);
    if (emotion === 'excited') {
      pulse += 0.02;
      warp += 0.02;
    }

    if (starWaveTimer > 0) {
      const waveBoost = (starWaveTimer / STAR_WAVE_DURATION) * 0.12;
      brightness += waveBoost;
      warp += waveBoost * 0.5;
      starWaveTimer = Math.max(0, starWaveTimer - delta);
    }

    const lerpFactor = delta * 2.5;
    currentValues.starBrightness = THREE.MathUtils.lerp(currentValues.starBrightness, brightness, lerpFactor);
    currentValues.starWarp = THREE.MathUtils.lerp(currentValues.starWarp, warp, lerpFactor);
    currentValues.starPulse = THREE.MathUtils.lerp(currentValues.starPulse, pulse, lerpFactor);

    mat.uniforms.uStarBrightness.value = currentValues.starBrightness;
    mat.uniforms.uStarWarp.value = currentValues.starWarp;
    mat.uniforms.uStarPulse.value = currentValues.starPulse * (1 + Math.sin(uniforms.uTime.value * 6) * 0.25);
  };

  const animate = () => {
    const delta = clock.getDelta();
    frameId = requestAnimationFrame(animate);

    if (pushBackSignal !== lastPushBackSignal) {
      lastPushBackSignal = pushBackSignal;
      pushBackActive = true;
      pushBackElapsed = 0;
      starWaveTimer = STAR_WAVE_DURATION;
    }

    uniforms.uTime.value += delta;
    updateStateValues(delta);
    updateCamera(delta);
    updateStars(delta);

    if (starsPoints) {
      let spin = 0.01;
      if (state === 'thinking') spin = 0.018;
      if (state === 'responding') spin = 0.013 + currentValues.voiceLevel * 0.01;
      if (emotion === 'excited') spin *= 1.25;
      if (emotion === 'sad') spin *= 0.75;
      starsPoints.rotation.y += delta * spin;
    }

    composer?.render();
  };

  onMount(() => {
    initScene();
    resizeObserver = new ResizeObserver(() => resizeRenderer());
    if (container) {
      resizeObserver.observe(container);
    }
    window.addEventListener('resize', resizeRenderer);
    animate();

    return () => {
      if (frameId) cancelAnimationFrame(frameId);
      window.removeEventListener('resize', resizeRenderer);
      resizeObserver?.disconnect();
      renderer?.dispose();
      composer?.dispose();
      particlePoints?.geometry.dispose();
      (particlePoints?.material as THREE.Material | undefined)?.dispose();
      starsPoints?.geometry.dispose();
      (starsPoints?.material as THREE.Material | undefined)?.dispose();
      renderer?.domElement?.parentNode?.removeChild(renderer.domElement);
    };
  });

  onDestroy(() => {
    if (frameId) cancelAnimationFrame(frameId);
    window.removeEventListener('resize', resizeRenderer);
    resizeObserver?.disconnect();
  });
</script>

<div class="absolute inset-0" aria-hidden="true" bind:this={container}></div>
