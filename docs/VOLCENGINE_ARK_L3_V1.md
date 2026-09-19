# Volcengine Ark / Doubao L3 adapter v1

This adapter is a strict, non-production implementation of the Ark Chat
Completions structured-output contract for the L3 research controller.

## Official contract checked

The official Ark documentation describes its OpenAI-compatible endpoint as
`POST https://ark.cn-beijing.volces.com/api/v3/chat/completions`, with bearer
authentication. The `model` field is the Ark endpoint ID. Its structured output
uses `response_format.type=json_schema` and a `json_schema` object containing
`name`, `schema`, and `strict`.

Sources checked 2026-09-20:

- [Ark documentation landing page](https://www.volcengine.com/docs/82379/66619f8df281250274ef4f88?lang=zh)
- [official Volcengine model announcement](https://developer.volcengine.com/articles/7610285824933445675)

The maintained contract fingerprint is an audit marker for this credential-free
note. It is not a secret, price quote, or assertion that a model endpoint is
enabled in an account.

## Model choice

The default family is `doubao-seed-2-0-lite`: Volcengine positions the Lite
variant as the cost/latency-oriented general model, while Pro is for the most
complex reasoning. L3 is bounded Chinese research summarisation with rigid JSON
output, so Lite is the conservative default. A deployment must pass the actual
versioned Ark endpoint ID and price-catalog version; neither is guessed or
embedded in source.

## Safety boundary and costs

- the constructor accepts no API key or `production_ready` parameter;
- `generate()` validates input privacy review and execution contract, then fails
  before any HTTP request, budget reservation, or account interaction;
- the inspectable body is one non-streaming request, temperature zero, strict
  schema, and no retry policy;
- completed-response mapping requires exact task/prompt/schema/fingerprint and
  evidence-modality echo before calculating estimated token cost;
- upstream errors and raw bodies are not emitted by this module.

For a local wiring smoke (no paid call, no secret echo):

```sh
uv run python scripts/volcengine/local_smoke.py asr-readiness
uv run python scripts/volcengine/local_smoke.py ark-contract
```

The only local secret names reserved for a later separately reviewed live smoke
are `VOLCENGINE_ASR_API_KEY` (Doubao Voice new-console key) and
`VOLCENGINE_ARK_API_KEY` (Ark API key). Neither is loaded by the adapter or
persisted by the smoke entry. A future paid implementation must additionally
receive a reviewed endpoint ID, media URL policy, price catalog and explicit
one-call confirmation.
