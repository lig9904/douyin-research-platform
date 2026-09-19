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


## 11. 接口实现优先级（深挖后修正）

### 默认实时研究：极速版

优先接口：

```text
POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash
```

特点：
- 单次请求直接返回结果
- 不需要 submit/query 轮询
- 音频时长 ≤ 2h
- 音频大小 ≤ 100MB
- 支持 WAV / MP3 / OGG OPUS
- audio.url 与 audio.data(base64) 二选一
- 返回 utterances 句级时间戳
- 返回 words 逐词时间戳
- 官方示例显示 30 分钟音频通常约 10 秒返回（不含传输）

对我们 30–120 秒短视频最合适。

资源 ID：
`volc.bigasr.auc_turbo`

新控制台鉴权优先：
`X-Api-Key`

旧控制台才使用 App-Key + Access-Key。

### 批量历史回填：闲时版

只有大量 backfill 且正式价格确实更低时才考虑：

```text
POST /api/v3/auc/bigmodel/idle/submit
POST /api/v3/auc/bigmodel/idle/query
```

特点：
- submit/query
- 24h 内返回
- 适合可延迟任务
- 增加轮询状态机复杂度

所以 V1 不默认启用。

## 12. URL 与临时文件策略

极速版支持：
- `audio.url`
- `audio.data`

实际流程：

1. 先尝试直接将短时有效媒体 URL 交给 ASR。
2. 若因防盗链、过期、跨域抓取失败：
   - worker 临时下载视频/音频
   - FFmpeg 转为单声道 MP3/WAV
   - 文件较小时 base64 直接提交
   - 识别后立即删除临时文件

不为了 ASR 永久保存视频。

## 13. 请求追踪与错误

每次调用必须保存：
- request_id
- X-Tt-Logid
- X-Api-Status-Code
- resource_id
- duration_ms
- retry_count

常见状态按官方接口：
- 20000000 success
- 20000003 silent audio
- 45000001 invalid request
- 45000002 empty audio
- 45000151 invalid format
- 550xxxx internal error
- 55000031 server busy

重试只针对明确的临时服务错误；参数/格式错误不重试。

## 14. 热词实现

热词通过火山引擎自学习平台维护，识别请求传入热词表 ID/名称。

当前官方限制：
- 每应用最多 500 个词表
- 每词表最多 5000 个热词
- 单个热词少于 10 个字
- 权重 1–10，默认 4
- 一个识别请求只启用一张词表

V1 只维护一张“九九项目词表”，版本化记录：
- hotword_table_id
- hotword_version
- updated_at

不要频繁按视频创建新词表。


## 15. 本地 ASR 深挖后的最终结论（2026-09-19）

继续核验了两个成熟本地候选：

### SenseVoiceSmall / FunASR

- SenseVoiceSmall：234M 参数
- 中文 / 粤语 / 英文 / 日文 / 韩文
- 官方中文 benchmark 相对 Whisper 有优势
- FunASR 当前 release：1.4.16（2026-09-18）
- FunASR/SenseVoice repo 源码 MIT
- 官方 SenseVoiceSmall 权重采用 FunASR Model Open Source License Agreement v1.1
- 官方维护者已澄清：遵守该模型协议时允许商业使用官方 SenseVoiceSmall 权重，但需保留出处/作者/模型名称

### faster-whisper

- 当前 release：1.2.1
- MIT
- Python >=3.9
- CPU int8 可运行
- 官方 benchmark：i7-12700K/8线程，Whisper-small 13分钟音频约1m42s、约1.48GB RAM
- 多语种、VAD、时间戳生态成熟

### 为什么 V1 仍不自建

火山引擎官网当前豆包语音识别模型 2.0：

- 录音文件识别：0.8 元/小时
- 流式语音识别：1.0 元/小时

本项目只对 L2/L3 少量视频 ASR。

估算示例：

```text
100 条/月 × 平均 1 分钟 = 1.67 小时/月
ASR ≈ 1.34 元/月

500 条/月 × 平均 1 分钟 = 8.33 小时/月
ASR ≈ 6.67 元/月

100 小时/月
ASR ≈ 80 元/月
```

即使实际价格因套餐/版本不同需要在控制台复核，量级已经足够说明：

**V1 的主要成本不是 ASR。**

因此为了省每月几元到几十元而维护：
- 专用 worker image
- 模型权重
- CPU/GPU 资源
- 模型升级
- 中文准确率回归
- 模型许可证/归属
- 冷启动/缓存

性价比不成立。

### 最终决策

V1：
- 默认：火山引擎录音文件识别/极速版
- 不部署本地 ASR worker

后续触发以下任一条件再评估本地：
- ASR 月成本持续明显高于本地运维成本
- 音频不能出园区
- 云 ASR 稳定性成为瓶颈
- 需要离线批量超大规模转写
- 需要 SenseVoice 特有的中文/粤语/情感事件能力

未来本地 Provider 优先级：
1. SenseVoiceSmall（中文优先）
2. faster-whisper（多语种兜底）

Provider Contract 保持可替换，因此未来切换无需改上层研究流程。
