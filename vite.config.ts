import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { createRequire } from 'module';
import fs from 'fs';
import { dirname, resolve } from 'path';

import { viteStaticCopy } from 'vite-plugin-static-copy';

const require = createRequire(import.meta.url);

// Resolve the onnxruntime-web version that MicVAD actually uses.
// Priority: the copy bundled inside @ricky0123/vad-web (it brings its own ORT
// dependency because prerelease versions don't satisfy its semver range);
// fallback to the top-level install.
const resolveOrtPkgPath = () => {
	const candidates = [
		'@ricky0123/vad-web/node_modules/onnxruntime-web',
		'onnxruntime-web'
	];

	for (const candidate of candidates) {
		try {
			const entry = require.resolve(candidate);
			const pkgPath = resolve(dirname(entry), '../package.json');
			if (fs.existsSync(pkgPath)) {
				return pkgPath;
			}
		} catch (err) {
			// keep trying next candidate
		}
	}

	throw new Error('Unable to resolve onnxruntime-web/package.json');
};

const ortPkgPath = resolveOrtPkgPath();
const onnxruntimeWebPkg = JSON.parse(fs.readFileSync(ortPkgPath, 'utf-8'));
const ONNXRUNTIME_WEB_VERSION: string = onnxruntimeWebPkg.version;
const ortDistGlob = resolve(dirname(ortPkgPath), 'dist/*.jsep.*');

export default defineConfig({
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: ortDistGlob,

					dest: 'wasm'
				}
			]
		})
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build'),
		ONNXRUNTIME_WEB_VERSION: JSON.stringify(ONNXRUNTIME_WEB_VERSION)
	},
	build: {
		sourcemap: true
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
});
