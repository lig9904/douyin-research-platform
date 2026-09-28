import { build } from 'esbuild'

// Windmill generates ./wmill at publication time. Check that all checked-in
// React/TSX/CSS can bundle without claiming a local mock is a live backend.
await build({
  entryPoints: ['index.tsx'],
  bundle: true,
  platform: 'browser',
  format: 'iife',
  outdir: '/tmp/douyin-research-app-build-check',
  write: false,
  logLevel: 'warning',
  plugins: [{
    name: 'windmill-virtual-bridge',
    setup(plugin) {
      plugin.onResolve({ filter: /^\.\/wmill$/ }, () => ({ path: 'windmill-virtual', namespace: 'windmill-virtual' }))
      plugin.onLoad({ filter: /.*/, namespace: 'windmill-virtual' }, () => ({
        contents: 'export const backend = new Proxy({}, { get: () => async () => { throw new Error("local build only") } })',
        loader: 'js',
      }))
    },
  }],
})
