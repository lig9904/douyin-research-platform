import { build } from 'esbuild'
import { createRequire } from 'node:module'

const result = await build({
  entryPoints: ['tests/SubjectProfilePanel.test.ts'],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  write: false,
  logLevel: 'warning',
  plugins: [{
    name: 'subject-profile-test-backend',
    setup(build) {
      build.onResolve({ filter: /^\.\/wmill$/ }, () => ({ path: 'wmill', namespace: 'subject-profile-test-backend' }))
      build.onLoad({ filter: /.*/, namespace: 'subject-profile-test-backend' }, () => ({
        contents: 'export const backend = {}',
        loader: 'js',
      }))
    },
  }],
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports,
)
