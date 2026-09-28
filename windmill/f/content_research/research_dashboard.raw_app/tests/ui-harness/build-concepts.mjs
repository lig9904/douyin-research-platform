import { build } from 'esbuild'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

await build({
  entryPoints: ['tests/ui-harness/concepts.tsx'],
  outfile: join(tmpdir(), 'douyin-concepts-ui-044-bundle.js'),
  bundle: true, platform: 'browser', format: 'iife', logLevel: 'warning',
  plugins: [{
    name: 'mock-windmill-backend',
    setup(plugin) {
      plugin.onResolve({ filter: /^\.\/backend$/ }, () => ({
        path: 'mock-backend', namespace: 'mock-backend',
      }))
      plugin.onLoad({ filter: /.*/, namespace: 'mock-backend' }, () => ({
        contents: 'export const backend = { project_creative_concepts: args => window.conceptMock(args) }',
        loader: 'js',
      }))
    },
  }],
})
