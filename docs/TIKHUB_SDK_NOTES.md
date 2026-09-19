# TikHub Python SDK Implementation Notes

日期：2026-09-19
核验版本：2.1.1

## 当前定位

TikHub 官方 Python SDK 适合做：
- endpoint 方法封装
- Bearer Token
- sync/async HTTP
- timeout
- proxy
- HTTP 异常映射
- 429/5xx/network retry

但它不是完整业务治理层。

Provider 仍必须负责：
- TikHub JSON envelope 校验
- 预算
- 缓存
- 全局限频
- 原始响应存储
- normalize
- 分页上限
- SDK/REST fallback
- 数据质量检查

## Retry 源码核验

默认：
- timeout: 30s
- max_retries: 3
- backoff_base: 0.5s
- backoff_max: 30s
- jitter: 0.25

重试：
- network / timeout / proxy connection error
- 5xx server/upstream
- 429

不重试：
- 除 429 外普通 4xx
- validation/user abort

429:
- 如果有 Retry-After，优先按 Retry-After sleep
- 否则 exponential backoff

注意：
实现中 attempt 从1开始，并在 `attempt >= max_retries` 时停止，因此默认 max_retries=3 实际是最多3次总尝试，不是“初次 + 3次重试”。

## Response Envelope 风险

SDK `_raise_for_status` 仅检查 HTTP status。

HTTP 2xx 后 `_decode` 当前直接返回 JSON。

因此 Provider 必须再校验 TikHub body，例如：

```json
{
  "code": 200,
  "request_id": "...",
  "message": "...",
  "data": ...
}
```

不得把 HTTP 200 自动等价为业务成功。

建议统一：

```python
def validate_tikhub_envelope(resp: dict) -> dict:
    if not isinstance(resp, dict):
        raise ProviderProtocolError(...)
    if resp.get("code") != 200:
        raise ProviderBusinessError(
            code=resp.get("code"),
            message=resp.get("message"),
            request_id=resp.get("request_id"),
            raw=resp,
        )
    return resp
```

若具体 endpoint 的 data 内又有抖音上游 `code/status_code`，normalizer 再按 endpoint contract 判断。

## SDK Version

当前 pyproject：
- tikhub 2.1.1
- Python >=3.9
- Development Status: Alpha
- httpx >=0.27
- pydantic >=2.6

V1：
- 固定 `tikhub==2.1.1`（直到 V0 测试完成）
- 不使用 floating latest
- 升级 SDK 必须跑 Provider regression tests

## SDK Coverage Gap

在线 API 文档可能领先于 SDK 生成版本。

已确认例：
- `/api/v1/douyin/creator/fetch_creator_material_center_related`
  在线文档存在
  当前 SDK 2.1.1 repository 未检索到 generated method

因此必须保留 REST fallback。

## Logging

SDK 自己会记录：
- HTTP status
- latency
- request id
- retry warning

但业务仍要写：
- external_api_call
- external_api_response
- estimated/actual cost
- cache hit
- normalization result
- source endpoint

不要依赖 SDK log 作为业务审计记录。

## Source

TikHub official SDK:
https://github.com/TikHub/TikHub-API-Python-SDK
