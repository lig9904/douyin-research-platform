# 72 小时候选增量与单条详情覆盖

## 实际执行

经浏览器 JumpServer，在服务器独立源码目录使用已合并 `4bd8cbd`。
未发布研究台、改动定时配置或执行 ASR/L3；密钥仅经已有受控配置管道传递。

- 第二个既有视频单条详情探针：响应存档 #20，1 次 HTTP、零重试，报价估算 USD 0.001。
  标题、描述、时长、作者与赞评转藏字段均存在，退出码 0。
  前一个样本 #19 无有效详情，因此不能据此宣布单条路由普遍可替代 batch50。
- 复用既有 Golden 入口：`max_items=5,date_window_hours=72,page=1,detail_strategy=batch50`，
  `force_refresh=false`，无金额上限。实际 2 次未缓存 HTTP，估算 USD 0.051，已对账金额仍为空。
- 发现批次 `2249e143-8581-40ec-aeca-19c32b15076c` 成功，新增 2 条视频且完成 L1 评分。
  榜单响应 #21；批量详情响应 #22 的 `aweme_details` 为长度 2 的数组，`filter_list=null`。
  两条新视频分别具有榜单与详情指标快照，时长约 13 秒、73 秒。

## 后续链路

- 既有 `process_comment_batch` 任务 `01a0c24c-d04d-90b5-85a1-5c1f9255569d`
  完成且 success=true；评论/L2 批次 `1231a78d-8abc-44ca-bfc3-a310b2b32b24`
  绑定上述发现批次，处理 2 条，晋级候选 1 条。晋级不等于人工音频或正文审核通过。
- 73 秒视频 `ed72580d-6ec3-41db-985f-0750e3b62dc7` 的既有媒体任务
  `01a0c24d-d353-8d15-6663-be7109ad8909` 完成且 success=true，媒体批次
  `fe3a21c8-9de3-4b01-96ca-dd821597c3cf`。返回两个新 asset ID，`reused=false`，
  `external_paid_calls=0`。此处证据为任务持久化结果，尚未完成本次新增对象独立 GET/试听验收。

## 证据与未完成项

服务器 `/srv/douyin-research-test/evidence/` 保留：

- `single-detail-second-sample-20260921.log`
- `candidate-window-72h-20260921.log`、`candidate-window-72h-audit-20260921.log`
- `candidate-comments-launch-20260921.log`、`candidate-comments-result-20260921.log`
- `candidate-media-launch-20260921.log`、`candidate-media-result-20260921.log`

本次单条详情与发现已知报价估算合计 USD 0.052，不包括评论、既有定时调用等其他消费，
不能用作当日实扣总额。每日实际金额仍以供应商日账同步为准。
两个新候选不是旧素材重复抓取，但内容充分性仍需试听；尚未审核、转写或生成新分析。
当前每小时任务仍保持旧 24 小时设置，本轮不证明定时候选来源已改善。

## 后续对象与播放器验收

新增两个对象已独立执行内网流式 GET，逐块计算 SHA-256 并核对数据库大小：
音频 2,340,696 字节、视频 23,318,765 字节，共 25,659,461 字节，两项全部匹配。
数据库使用只读事务，未落盘媒体、未改动对象；日志
`candidate-media-content-20260921.log`，退出码 0，`MEDIA_CONTENT_VERIFIED`。

真实研究台刷新后显示 5 条视频，选择 73 秒新候选并加载音频、刷新试听链接。
DOM 播放器 `duration=73.142875`、`readyState=4`、`error=null`；点击播放后
`currentTime=12.520557`、`paused=false`，随后暂停。审核仍为“未审核”，复选框未勾选。
这证明新增音频可加载和播放，不代表人工已核对内容或批准云端转写。

页面顶部视频预览仍为占位区，不能把音频播放验收扩展为视频预览通过；该缺口继续处理。
