import { build } from 'esbuild'
import { createRequire } from 'node:module'

const result = await build({
  entryPoints: ['tests/VideoMediaPreview.test.tsx'], bundle: true,
  platform: 'node', format: 'cjs', write: false, logLevel: 'warning',
  plugins: [{ name: 'test-backend', setup(build) {
    build.onResolve({ filter: /^\.\/wmill$/ }, () => ({ path: 'wmill', namespace: 'test-backend' }))
    build.onLoad({ filter: /.*/, namespace: 'test-backend' }, () => ({
      contents: 'export const backend = {video_media_preview() {throw new Error("unexpected request during render")}}',
      loader: 'js',
    }))
  }}],
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports,
)
