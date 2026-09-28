import { build } from 'esbuild'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

await build({
  entryPoints: ['tests/ui-harness/las-review.tsx'],
  outfile: join(tmpdir(), 'douyin-las-ui-041-bundle.js'),
  bundle: true, platform: 'browser', format: 'iife', logLevel: 'warning',
  plugins: [{
    name: 'mock-windmill-backend',
    setup(plugin) {
      plugin.onResolve({ filter: /^\.\.\/\.\.\/backend$/ }, () => ({
        path: 'mock-backend', namespace: 'mock-backend',
      }))
      plugin.onLoad({ filter: /.*/, namespace: 'mock-backend' }, () => ({
        contents: 'export const backend = { project_las_video_review: args => window.lasMock(args), project_video_media_preview: async () => ({has_media: true, playback_url: "https://example.test/project-preview-fixture.mp4", expires_in_seconds: 300}) }',
        loader: 'js',
      }))
    },
  }],
})
