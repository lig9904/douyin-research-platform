import React from 'react'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import ProjectVideoMediaPreview from '../src/components/ProjectVideoMediaPreview'

const html = renderToStaticMarkup(<ProjectVideoMediaPreview projectId="test-project" videoId="test-video" />)
assert.match(html, /本项目视频预览/)
assert.match(html, /仅播放本项目已纳入的公开视频副本/)
assert.match(html, /跨项目共享不包含媒体/)
assert.doesNotMatch(html, /<video|src=/)

const component = readFileSync('src/components/ProjectVideoMediaPreview.tsx', 'utf8')
assert.match(component, /\[projectId, videoId\]/)
assert.match(component, /project_id: projectId, video_id: videoId/)
assert.match(component, /seekRequest\.projectId !== projectId \|\| seekRequest\.videoId !== videoId/)
assert.match(component, /video\.currentTime = seconds/)
assert.match(component, /onLoadedMetadata=\{applySeek\}/)
assert.doesNotMatch(component, /backend\.video_media_preview\(/)
const library = readFileSync('VideoLibrary.tsx', 'utf8')
assert.match(library, /<ProjectVideoMediaPreview key=/)
console.log('ProjectVideoMediaPreview project-scoped initial rendering checks passed')
