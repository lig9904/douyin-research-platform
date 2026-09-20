# 阿里云与火山：语音转写、案例研究成本对照

核验日期：2026-09-20。以下均为人民币公开按量原价，不含免费额度、活动、合同优惠、
音频中继存储/流量。阿里以华北2（北京）为口径；不能把海外地域价套给北京 Key。
这是选型与调用规则研究，未切换当前 Provider，也没有在本次研究中发送付费推理请求。
TikHub 数据采集不是这些模型的替代品，完整业务链见 [接口业务策略](API_BUSINESS_CALL_POLICY_V1.md)。

## 1. 录音文件转写：按相同计费时长比较

下表假设单音轨、计费语音覆盖全长；阿里部分模型会剔除非语音段，真实同一文件的计费时长可能不同。

| 供应商 / 精确服务或 model ID | 官方计费单位 | 折合元/小时 | 60秒 | 1000条×60秒 |
| --- | ---: | ---: | ---: | ---: |
| 火山 SeedASR 2.0，资源 `volc.seedasr.auc` | 0.8元/小时 | 0.800 | 0.013333 | 13.333333 |
| 阿里 `qwen-audio-3.0-asr-flash-filetrans` | 0.00022元/秒 | 0.792 | 0.013200 | 13.200000 |
| 阿里 `qwen3-asr-flash-filetrans` | 0.00022元/秒 | 0.792 | 0.013200 | 13.200000 |
| 阿里 `fun-asr` | 0.00022元/秒 | 0.792 | 0.013200 | 13.200000 |
| 阿里 `paraformer-v2` | 0.00008元/秒 | 0.288 | 0.004800 | 4.800000 |
| 火山旧大模型标准 / 极速 / 闲时 | 2.3 / 4.5 / 1.2元/小时 | 2.3 / 4.5 / 1.2 | — | — |

来源：[阿里模型定价的语音识别部分](https://help.aliyun.com/zh/model-studio/model-pricing)、
[火山语音计费](https://docs.volcengine.com/docs/DoubaoVoice/Billinginstructions-21?lang=zh)。
火山按毫秒累计时长折小时；阿里说明按音频秒数、输出不收费，但本次未取得最小计费粒度/
不足一秒取整规则的明确证据。因此上表是等时长报价，不是承诺任意短文件的最终账单。

### 输入、输出与接口协作

| 候选 | 输入与容量 | 研究业务所需输出 / 差异 |
| --- | --- | --- |
| SeedASR 2.0 标准 | 1个可下载音频URL；raw/wav/mp3/ogg；本次标准页未明确单文件最大时长/大小 | `show_utterances`取得句时间戳；与当前火山适配方向一致 |
| Qwen Audio 3.0 Filetrans | `file_urls`数组，但单次仅1个公网URL；≤12小时/2GB | 热词、上下文、时间戳和说话人分离；当前官方新推荐的文件转写候选 |
| Qwen3 ASR Filetrans | 单个`file_url`；≤12小时/2GB | 句时间戳；`enable_words=true`获取支持语种的字时间戳；情感信息；不支持说话人分离 |
| Fun-ASR | `file_urls`数组，当前共用HTTP参考限制单次1个URL；≤12小时/2GB | 时间戳、说话人分离；`fun-asr`当前对应`fun-asr-2025-11-07`，不与同步Flash混用 |
| Paraformer V2 | `file_urls`数组，单次仅1个URL；≤12小时/2GB | 时间戳、说话人分离；作为中文低成本基线验证，不因便宜预判质量 |

本表阿里模型均按单文件任务组织，字段名为数组也不代表可以批塞100个URL。
后续若选支持多文件的其他服务，也必须累计所有计费音频，不能误按1条时长收费。
来源：[Paraformer请求参数](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-python-sdk)。
Qwen Audio 3.0/Fun的HTTP说明还明确：指定多个`channel_id`时各音轨独立计费，
按`content_duration`计量语音内容而非直接取原文件总长；同时保存`usage.duration`和原始毫秒时长对账。
默认只处理第一个音轨；纯背景声不可伪记为已取得可用转写。
来源：[录音HTTP接口计量字段](https://help.aliyun.com/zh/model-studio/fun-asr-recorded-speech-recognition-http-api)。
说话人分离要求单声道，官方建议时长≤2小时。默认敏感词处理可能改变转写原文，
保存参数版本与处理标记，不能把被替换文本当作完整逐字证据。
普通`qwen3-asr-flash`的OpenAI兼容返回不带时间戳，不适合作为本项目证据转写的直接替代。
来源：[ASR模型/容量](https://help.aliyun.com/zh/model-studio/asr-model)、
[非实时接口及时间戳](https://help.aliyun.com/zh/model-studio/non-realtime-speech-recognition-user-guide)、
[火山标准接口](https://docs.volcengine.com/docs/DoubaoVoice/LargemodelrecordingfilerecognitionstandardversionAPI?lang=zh)。

调用流程：

- 火山：`POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit`；
  同任务`POST /api/v3/auc/bigmodel/query`；新Key用`X-Api-Key`，资源ID如上。
- 阿里：当前北京官方示例的基地址为`https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`；
  `POST /api/v1/services/audio/asr/transcription`，Bearer Key、`X-DashScope-Async: enable`；
  `GET /api/v1/tasks/{task_id}`查询，成功后下载结果JSON。WorkspaceId、Key和模型权限必须来自实际账号，
  不猜填；当前HTTP文档说明旧DashScope域名仍兼容，推荐新Workspace域名；实际账号需核对地域。
- 两家均把业务任务、submit、poll、下载分开记；排队只轮询原任务，不重复提交。
  官方按时长报价未单列状态查询费，实际有无独立扣款待账单对齐；不能将每次poll都再次乘音频费。
- 持久化音频指纹、实际时长、模型/参数版本、供应商任务ID、结果与本地存储路径，
  结果URL有时效，应及时保存；定时任务不需要人工浏览器登录。

### 业务判断

Qwen Audio 3.0、Qwen3与Fun比火山2.0只便宜1%，1000分钟差约0.13元，
这里指相同计费时长的单价比较，不包含模型剔除非语音段带来的usage差异。
不值得仅为这点单价重做当前链路；价值应来自更好的专有名词、方言、说话人或时间戳结果。
Paraformer比火山低64%，值得用相同已授权音频做质量基线。

但先跑便宜模型、失败再跑火山时，平均成本为`0.288 + 升级比例×0.8`元/小时；
升级比例达到64%就不再比直接火山便宜（等计费时长，未计质检、等待和工程成本）。
因此推荐普通话清晰样本优先评测Paraformer，强配乐、口音、专有名词样本重点比较
SeedASR 2.0与Qwen Audio 3.0；Qwen3情感字段仅作线索，不当事实结论。

## 2. 文本案例研究：在线、无缓存、非思考口径

固定算例为8,000输入token + 2,000计费输出token，不含图像/音频、工具调用和搜索。
不同模型对同一段文本的token数可能不同；下面是同usage对照，真实同样本应读取各自usage。

| 模型 / 输入档 | 输入元/百万token | 输出元/百万token | 单案例元 | 1000案例元 |
| --- | ---: | ---: | ---: | ---: |
| 阿里 `qwen-flash`，≤128k | 0.15 | 1.5 | 0.0042 | 4.20 |
| 火山 Seed 2.0 Mini，≤32k | 0.2 | 2.0 | 0.0056 | 5.60 |
| 阿里 `qwen-plus`，非思考、≤128k | 0.8 | 2.0 | 0.0104 | 10.40 |
| 火山 Seed 2.0 Lite，≤32k | 0.6 | 3.6 | 0.0120 | 12.00 |
| 阿里 `qwen-plus`，思考、≤128k，仅作价差提醒 | 0.8 | 8.0 | 0.0224 | 22.40 |
| 火山 Seed 2.0 Pro，≤32k | 3.2 | 16.0 | 0.0576 | 57.60 |

来源：[Qwen Flash](https://help.aliyun.com/zh/model-studio/qwen-flash)、
[Qwen Plus](https://help.aliyun.com/zh/model-studio/qwen-plus)、
[火山模型价格](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh)。
Plus思考模式真实输出还可能包含更多计费推理token，不能用同样2,000token保证真实总价。
模型名称不等于质量排名；上述价差不是效果评测结论。

### 长上下文、缓存和批量

| 阿里北京模型 / 输入档 | 常规输入/输出 | 缓存命中输入 | Batch File输入/输出 |
| --- | ---: | ---: | ---: |
| qwen-flash ≤128k | 0.15 / 1.5 | 0.03 | 0.075 / 0.75 |
| qwen-flash (128k,256k] | 0.6 / 6 | 0.12 | 0.3 / 3 |
| qwen-flash (256k,1m] | 1.2 / 12 | 0.24 | 0.6 / 6 |
| qwen-plus 非思考 ≤128k | 0.8 / 2 | 0.16 | 0.4 / 1 |
| qwen-plus 非思考 (128k,256k] | 2.4 / 20 | 0.48 | 1.2 / 10 |
| qwen-plus 非思考 (256k,1m] | 4.8 / 48 | 0.96 | 2.4 / 24 |

单位均元/百万token，缓存列指隐式命中；不得把总输入和命中输入重复收费。
按整次请求的输入长度档计价，不把超过阈值的部分单独当累进阶梯。
北京`qwen-flash`、`qwen-plus`别名文档支持缓存和批量；对应快照
`qwen-flash-2025-07-28`、`qwen-plus-2025-12-01`的能力表应单独读取，
不能因模型能力等同就把别名的缓存/Batch能力复制给快照。

阿里Batch File是单独的异步文件作业，不能把普通同步Chat请求记成半价。
同页Batch Chat列价与常规相同，也不是“所有批量都五折”；Batch File与缓存优惠不能想当然叠乘。
显式缓存的创建和命中另有价项，应在真实高复用前缀出现后独立估算，不提前保证命中率。
例如≤128k档：Flash显式创建0.188、命中0.015；Plus创建1、命中0.08元/百万token。
来源：[上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache)及上方两模型价表。

火山Lite的三档、音频、缓存存储价见[业务策略第5节](API_BUSINESS_CALL_POLICY_V1.md#5-评论语音与深研)。
Lite低优/Batch无缓存输入输出为常规50%，缓存命中不再折半。
同算例异步报价：Flash Batch File约0.0021元、Plus约0.0052元、Lite Batch约0.006元。
这些只是相应服务模式的报价；本项目尚未接入这些批作业，不代表当前运行账单。

### 输入输出与调用规则

阿里当前官方北京兼容接口：
`POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions`；
服务端Bearer Key，显式model、messages、非思考设置和结构化输出参数。
来源：[首次调用千问API](https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen)。
火山当前项目方向为`POST https://ark.cn-beijing.volces.com/api/v3/chat/completions`。
只有协议相似，不意味着更换URL即可通过现有Provider契约和响应校验。

推荐先以Flash作为有价可查的低成本候选，与现有Lite同题比较；通过质量验收后再决定默认路由。
Plus作为复杂案例候选，不对每个Flash输出重复审阅。
按同usage、先Flash后Lite的均价为`0.0042 + 升级比例×0.012`，升级率低于65%时
才比全部直接Lite便宜；返工、人审和延迟另计。
最终结果相同输入可本地复用；缺少证据应补数据，不能只升级更贵模型。

对普通研究案例，输入版本化转写/评论摘要/关键统计，输出带证据引用的结构化结论；
不默认发整段音视频，也不默认开启联网搜索。音画联合任务另做多模态报价，
不将本页文本单价应用于图像或音频token。

## 3. 上线前如何比较“便宜且能用”

本次结论是报价与文档契约已核验，阿里接入、质量、账号实际优惠/用量和账单均未验收。
不为研究价格创建新Key、充值或上传素材。

| 业务验收 | 固定输入 | 输出与通过依据 |
| --- | --- | --- |
| ASR | 同一组已授权音频，覆盖清晰普通话、配乐、口音、地名/品牌和多人 | 逐字错误、专名错误、漏句、时间戳可定位率、人工修订时间、任务耗时、真实计费时长 |
| L3 | 同版本证据包、Prompt与JSON Schema | Schema通过率、事实/引用准确性、证据不足时拒绝臆断、业务可用率、延迟、输入/输出/缓存usage |
| 成本 | 同任务的submit/poll/重试/升级记录 | 原币估算、供应商实账、折扣与免费抵扣分列；失败/超时未知项不冒记0 |

每次记录`provider/region/model_revision/service_mode/price_version`。
升级或模型别名变化后重新小样本比较；不把免费试用额度当长期单价。
最终依据“每个合格案例/每分钟合格转写的总费用”，不是一次请求最低报价。
