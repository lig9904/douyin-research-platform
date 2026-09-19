# Visual Analysis Pipeline V1

日期：2026-09-19

## 1. 结论

V1 视觉侧采用：

- FFprobe / FFmpeg：视频基础信息、首段关键帧、场景切换、临时抽帧
- PaddleOCR PP-OCRv6_small：本地屏幕文字 OCR
- Python：OCR 文本去重、时间段合并、视觉证据完整度计算
- 视觉大模型：只在 L3 极少数 Case 触发

不引入：
- PySceneDetect
- 全视频多模态模型
- 全量视频 OCR
- 视频永久存储
- GPU 视觉服务

## 2. 为什么不使用 PySceneDetect

FFmpeg 本身已提供：

- select + scene score
- scdet
- thumbnail
- ffprobe

官方文档对 `select='gt(scene,0.4)'` 给出示例，并说明 0.3–0.5 通常是合理的 scene threshold。

因此 V1 没必要再增加 PySceneDetect 依赖。

默认 scene threshold：
`0.35`

阈值必须可配置，并在真实短视频样本上调优。

## 3. 哪一级做视觉处理

### L0

不下载媒体。
不抽帧。
不 OCR。
不视觉模型。

### L1

默认仍不下载媒体。

只有晋级 L2 才进入视觉预处理。

### L2

低成本视觉处理：
- ffprobe
- hook keyframes
- scene frames
- 两阶段 OCR

### L3

只有必要时才：
- 生成视觉 contact sheet / 关键帧集
- 调视觉大模型

## 4. FFprobe 基础特征

进入 L2 后，先用 ffprobe 获取：

- duration_ms
- width
- height
- aspect_ratio
- fps
- frame_count（可得时）
- video codec
- audio_present
- audio codec
- bitrate（可得时）

这些全是确定性数据，不调用模型。

## 5. Hook Frames

短视频最重要的是前几秒。

固定抽：

- 0.1s
- 0.5s
- 1.0s
- 2.0s
- 3.0s
- 5.0s

如果视频不足对应时长，则自动裁剪。

另外始终抽：
- end frame：`duration - 0.5s`

用途：
- 首帧异常
- 标题卡
- 前3秒字幕
- 视觉反差
- 结尾留扣

不要只取第0帧，因为很多视频首帧可能是黑场/转场。

## 6. Scene Frames

使用 FFmpeg scene detection。

候选命令形式：

```bash
ffmpeg -i input.mp4   -vf "select='gt(scene,0.35)'"   -vsync vfr scene_%03d.jpg
```

V1 限制：
- 最多保留前 12 个 scene frames 用于后续分析
- 同一秒内多个 scene hit 做代码去重
- 记录 scene timestamp / scene score
- 不长期保存原图

如果场景切换过多：
- 不提高 OCR/视觉调用数量
- 只选择时间分布较均匀的代表帧

## 7. OCR 采用 PP-OCRv6

PaddleOCR 3.7.0 于 2026-06 发布 PP-OCRv6。

V1 默认：
- PP-OCRv6_small_det
- PP-OCRv6_small_rec
- CPU
- 优先高性能推理后端（在目标机器实测后决定 OpenVINO / ONNX / Paddle）

原因：
- small 在精度与速度之间更适合作为默认
- 单模型支持中文/英文/日文及多种拉丁文字
- 比 medium 更轻
- 比 tiny 精度更高

官方项目：
- Apache 2.0
- PaddleOCR 3.7.0

V0 要同时拿 tiny 做一次对比；如果短视频字幕上 tiny 已够用，则生产默认可以再降到 tiny。

## 8. OCR 两阶段漏斗

### Stage A：探测

只 OCR：

- 6 个 hook frames
- end frame
- 最多 6 个 scene frames

通常不超过 13 张。

计算：

- detected_text_frames
- unique_text_chars
- mean_ocr_confidence
- hook_text_density
- screen_text_presence

如果文字证据很弱，停止。

### Stage B：扩展 OCR

满足任一条件才扩展：

- hook/scene 中检测到明显文字
- ASR 很少，但视频研究优先级高
- 人工标记“文字驱动”
- L3 研究要求完整屏幕字幕

全片采样最多：

`OCR_MAX_FRAMES = 90`

采样策略：

1. 前 5 秒：0.5 秒一帧（hook 高密度）
2. 剩余时长：在剩余 frame budget 中均匀采样
3. 永远不超过 90 帧

120 秒视频也不会无限 OCR。

## 9. OCR 图像预处理

默认：

- 保持原纵横比
- 长边限制在适合 OCR 的范围，V0 实测 960 / 1280 两档
- 不提前只裁底部字幕区，因为抖音文字位置不固定
- 不做文档方向矫正/表格/公式等无关模块

对于低置信度文本，可做一次局部 bbox 放大再识别，而不是整帧再跑高价/大模型。

## 10. OCR 文本时间轴

不要只存“整条 OCR 文本”。

每次结果转换成：

```text
start_ms
end_ms
text
confidence
bbox
source_frame_count
```

代码处理：

1. Unicode/空白标准化
2. 相邻帧同文本合并
3. 高相似度文本合并
4. 保留原始文本
5. 给重复字幕形成时间区间

推荐先使用 Python 标准库做字符级相似度；
只有实测不够再引入 RapidFuzz 等依赖。

## 11. FFmpeg + OCR 可以得到的免费视觉特征

代码直接计算：

- scene_change_count
- scene_changes_per_minute
- hook_scene_change_count
- first_text_time_ms
- hook_text_count
- total_unique_screen_text
- ocr_text_density
- ocr_confidence
- screen_text_duration_ratio
- silent/nonverbal hint（结合 ASR）
- end_text_present

这些都进入结构化字段。

不让 LLM计算。

## 12. 视觉大模型触发条件

只允许 L3。

满足以下之一：

### A. 人工触发
`manual_visual_review = true`

### B. 高优先级 + 文本证据不足

例如：
- priority_score 高
- ASR 有效文本很少
- OCR 有效文本很少
- 但视频表现异常突出

这类很可能是：
- 视觉奇观
- 动作
- 表情
- 构图
- 无对白反差

### C. 研究问题本身是视觉关系

例如：
- 开头画面为什么吸睛
- 前后视觉反差
- 角色表情/动作
- 镜头信息揭示顺序
- 视觉异常
- 画面叙事

### D. Pattern 需要视觉证据

例如多个 Case 要验证：
- 先展示结果再解释
- 前3秒视觉反常
- 人物出场方式
- 大小/尺度反差

## 13. 视觉大模型输入

默认不传整条视频。

最多 6–8 张图：

优先：
1. 0.1/0.5s
2. 1s
3. 2s
4. 3s
5. 最高价值 scene frame 1
6. 最高价值 scene frame 2
7. 中后段代表 frame
8. end frame

可以代码生成 2×4 contact sheet，并烧录 timestamp。

同时附：
- ASR transcript
- OCR text timeline
- metrics
- scene timestamps

视觉模型主要负责理解“画面发生了什么”，不再要求它重新 OCR。

## 14. 为什么用 Contact Sheet

目的：
- 减少视觉模型图片数量
- 固定关键帧
- 保留时间顺序
- 控制成本
- 方便后续人工回看

如果单张 contact sheet 缩放后影响关键细节，再拆成两个 2×2 / 2×4 sheet。

## 15. 视觉模型输出

必须结构化，例如：

```json
{
  "visual_hook": {
    "type": null,
    "evidence_frames": []
  },
  "visual_turns": [],
  "character_actions": [],
  "visual_contrast": [],
  "shot_progression": [],
  "unresolved_visual_questions": []
}
```

禁止：
- 直接输出“爆款原因”
- 把视觉模型推断当事实
- 没有 frame timestamp 就写视觉证据

## 16. 媒体生命周期

V1 没有 NAS。

流程：

```text
temporary media
↓
ffprobe
↓
hook/scene/OCR frames
↓
OCR / optional visual model
↓
保存结构化结果和时间戳
↓
删除临时视频与图片
```

长期保存：
- metadata
- frame timestamps
- scene score
- OCR timeline
- visual analysis JSON
- source fingerprint

不长期保存：
- 原视频
- 临时抽帧

Web 研究台仍使用抖音/TikHub已有封面 URL 或原链接展示。

## 17. V1 Worker

不单独建设新服务。

Windmill 新增一个可选 `media` worker group/image：

包含：
- ffmpeg
- ffprobe
- PaddleOCR
- PaddlePaddle CPU runtime
- Python image dependencies

模型缓存 volume 持久化。

因为只处理 L2/L3，小规模 CPU worker 足够。

## 18. V0 真实样本验证

选 20–30 条样本：

- 大字幕口播
- 小字幕
- 动态花字
- 文字位于顶部/中部/底部
- 无字幕
- 纯视觉
- 快速剪辑
- 慢镜头
- 黑场开头
- 图文卡片
- AI动画
- 真人剧情

比较：

### Scene
- threshold 0.30 / 0.35 / 0.40
- scene frame数量
- 是否漏重要转场

### OCR
- PP-OCRv6 tiny vs small
- 960 vs 1280 resize
- OCR用时
- 字幕召回
- 错字率
- 专有词表现

最终用本项目样本决定参数。

## 19. 最终优先级

V1：

```text
FFprobe
→ Hook Frames
→ Scene Detection
→ OCR Stage A
→ 必要时 OCR Stage B
→ 极少数 L3 Visual LLM
```

视觉侧仍然遵循：

**代码优先、本地模型优先、先粗后细、大模型最后。**

## 20. 官方依据

FFmpeg 官方：
- select scene example：0.3–0.5 通常为合理阈值
- scdet 提供 scene score / timestamp metadata
- thumbnail 可选代表帧

PaddleOCR 官方：
- 2026-06-11 发布 PP-OCRv6
- tiny / small / medium 三档
- small/medium 支持 50 种语言
- Apache License 2.0
