# 报价研究快照

`tikhub-20260920.json`保存116个业务候选和费用接口的公开价格、请求契约摘要、
原始来源URL及字节SHA-256；同名Markdown是供人工阅读的逐项表。
不是运行时配置，不代表全部接口已启用。

生成器只读本地已下载的公开文件，不发起网络请求，不使用Key。

```sh
python scripts/tikhub/build_pricing_inventory.py \
  --tariffs /path/to/get_all_endpoints_info.json \
  --openapi /path/to/openapi.json \
  --output docs/pricing/tikhub-20260920
python -m pytest tests/test_pricing_inventory.py -q
```

来源是`https://api.tikhub.io/api/v1/tikhub/user/get_all_endpoints_info`和
`https://api.tikhub.io/openapi.json`的未认证GET；不要为抓取这两份公共文档附带用户凭据。
2026-09-20原始文件保存在本次研究工作区归档，未将全量第三方接口说明重新分发到仓库。
后续官网会变化，仅从GitHub克隆无法重新获取旧的原始字节；同原始文件重跑可字节复现，
只有派生JSON时可验证Markdown一致性，不能反向证明原始来源SHA。

生成器的日期、候选范围、接入状态与描述提取的容量均为本次研究版本，
不是自动更新价格的生产任务。未来重新调研应更新日期与业务状态、审查容量，保留旧快照。
缺报价保留null；响应字段覆盖率、有效样本率、折扣实际到账要另做运行验收。
