import React from 'react'
import assert from 'node:assert/strict'
import { renderToStaticMarkup } from 'react-dom/server'
import VideoMediaPreview from '../src/components/VideoMediaPreview'

// The virtual backend stub throws if rendering starts a request. Playback
// must remain an explicit user action rather than fetching on list render.
const html = renderToStaticMarkup(<VideoMediaPreview videoId="test-video" />)
assert.match(html, /私有视频预览/)
assert.match(html, /加载视频 \/ 刷新链接/)
assert.match(html, /不会触发采集或模型调用/)
assert.match(html, /播放不代表音频审核通过/)
assert.doesNotMatch(html, /<video|src=|媒体预览暂未接入/)
console.log('VideoMediaPreview initial rendering checks passed')
