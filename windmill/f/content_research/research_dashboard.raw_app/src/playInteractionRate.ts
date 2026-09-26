type VideoCounts = {
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  share_count?: number | null
}

// A display-only, fieldwise-merged estimate; never a platform retention,
// recommendation, or conversion metric. Missing components are not zeros.
export function formatPlayInteractionRate(video: VideoCounts): string {
  const values = [video.play_count, video.like_count, video.comment_count, video.share_count]
  if (values.some(value => typeof value !== 'number' || !Number.isFinite(value) || value < 0)) return '—'
  const [plays, likes, comments, shares] = values as number[]
  if (plays === 0) return '—'
  return `${((likes + comments + shares) / plays * 100).toFixed(1)}%`
}
