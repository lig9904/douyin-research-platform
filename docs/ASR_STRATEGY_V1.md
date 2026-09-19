# V1 ASR 与媒体预处理策略

日期：2026-09-19

## 1. 默认不自建 Whisper

V1 的转写量只覆盖 L2/L3 少量样本，自建 GPU/CPU ASR 服务的运维成本和复杂度不划算。

默认 Provider 建议：
- 火山引擎 / 豆包语音录音文件识别
- Provider 抽象保留未来替换能力

当前火山引擎官网显示豆包语音识别模型 2.0 录音文件识别价格已经非常低，适合按需调用。

## 2. 为什么适合短视频研究

大模型录音文件识别可返回：
- full text
- utterances
- sentence start/end timestamps
- word-level timestamps

所以能直接支持：
- 前 3 秒实际说了什么
- 每句话时长
- 信息密度
- 反转出现时间
- 结尾留扣位置

这比只保存纯 transcript 更适合后续结构分析。

## 3. 热词

火山引擎支持热词表。

项目建立专用词表，例如：
- 螭吻
- 九九
- 渔岛
- 独角龙
- 龙宫办事处
- 秦皇岛
- 相关神兽/地名/IP专名

原则：
- 只放真实专名和容易误识别词
- 不放普通高频词
- 词表版本化
- transcript 记录 hotword_version

## 4. Media -> ASR

V1 不永久保存视频。

流程：
1. TikHub detail 获取标准媒体 URL
2. 首选测试火山 ASR 是否能直接拉该 URL
3. 如果 URL 有防盗链/短时效问题：
   - Windmill worker 临时下载
   - FFmpeg 只抽音频
   - 以 audio.data / 临时 URL 方式提交 ASR
4. 保存 transcript + timestamps
5. 删除 worker 临时媒体

不调用最高画质下载接口，除非深研确有视觉需要。

## 5. ASR 模式

实时研究：
- 优先标准版/极速版，依据真实套餐价格与时效决定

历史 backfill：
- 可评估闲时版
- 允许 24h 返回
- 仅在价格确实更低时启用

不要为了几分钟时效把所有任务都送最高价模式。

## 6. ASR 触发条件

不满足以下任一条件则不转写：
- L1 晋级 L2
- 人工明确点击“转写”
- L3 深研缺 transcript
- 规则检测到文案不足、需要音频语义

已经有 freshness 内 transcript 时不重复 ASR。

## 7. 成本字段

transcript / analysis 必须记录：
- provider
- model/resource_id
- audio_duration_ms
- mode
- hotword_version
- cost_amount
- created_at

## 8. 纯代码媒体特征

在 LLM 前，可用 FFmpeg/ffprobe 免费得到：
- duration
- width/height
- fps
- audio presence
- frame count
- scene-change timestamps
- audio stream metadata

这些是确定性信息，不交给模型。

## 9. OCR 后置但预留

很多抖音视频的重要信息来自屏幕字幕。

V1 初期：
- 先用标题 + 文案 + ASR
- 如果实际样本证明屏幕文字缺失严重，再启用本地 OCR

推荐候选：
- PaddleOCR PP-OCRv6
- Apache 2.0
- tiny/small/medium 多档模型

OCR 方案：
- FFmpeg 定时抽帧
- PaddleOCR
- 文本去重
- 保留 text + timestamp
- 不把每一帧都送视觉大模型

## 10. VAD 暂不引入

Silero VAD 可本地判断语音段，MIT License。

但短视频一般时长有限，先引入 VAD 会增加：
- 音频切片
- 时间轴重映射
- 依赖

只有当出现大量长视频/音乐空白导致 ASR 成本明显增加时再加入。
