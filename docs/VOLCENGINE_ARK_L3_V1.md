# Volcengine Ark / Doubao L3 adapter v1

This adapter has two deliberately separate boundaries for the Ark Chat
Completions structured-output contract: the default provider is permanently
non-production, while the separately named verified-live provider is the
source-reviewed one-shot transport used by an explicit local smoke.

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

- `VolcengineArkL3Provider` accepts no API key or `production_ready` parameter;
  its `generate()` validates input privacy review and execution contract, then fails
  before any HTTP request, budget reservation, or account interaction;
- `VerifiedLiveVolcengineArkL3Provider` is an explicitly different class, with
  no runtime production toggle. It requires a non-empty API key in memory and
  an explicit `expected_response_model`; its `repr` and errors never include
  the key;
- the verified-live class makes exactly one `httpx` `POST` to the fixed HTTPS
  base/path, with `Authorization: Bearer`, `follow_redirects=false`, a fixed
  timeout and zero retries. It rejects every non-2xx or non-object/invalid JSON
  response without exposing provider bodies;
- the inspectable body is one non-streaming request, temperature zero, strict
  schema, and no retry policy;
- completed-response mapping requires the explicitly expected response `model`
  (Ark may echo an endpoint ID instead of the internal model revision), exact
  task/prompt/schema/fingerprint and
  evidence-modality echo before calculating estimated token cost;
- upstream errors and raw bodies are not emitted by this module.

For a local wiring smoke (no paid call, no secret echo):

```sh
uv run python scripts/volcengine/local_smoke.py asr-readiness
uv run python scripts/volcengine/local_smoke.py ark-contract
```

## One-call local live smoke

This is the only command in this module that can make a paid request. It is
intentionally protected by both an explicit `--live` flag and the exact
environment confirmation `ARK_LIVE_SMOKE=YES`; it never retries. Set the
following values only in the local secret environment (never in Git):

```sh
VOLCENGINE_ARK_API_KEY=...
VOLCENGINE_ARK_ENDPOINT_ID=...
VOLCENGINE_ARK_MODEL_REVISION=...
VOLCENGINE_ARK_EXPECTED_RESPONSE_MODEL=...
VOLCENGINE_ARK_PRICING_VERSION=...
VOLCENGINE_ARK_INPUT_COST_PER_MILLION=...
VOLCENGINE_ARK_OUTPUT_COST_PER_MILLION=...
ARK_LIVE_SMOKE=YES uv run python scripts/volcengine/local_smoke.py ark-live --live
```

`VOLCENGINE_ARK_EXPECTED_RESPONSE_MODEL` must be observed/reviewed for the
selected endpoint (it can be the endpoint ID or a serving revision). The smoke
only prints its one-call count, validation result, currency and estimated cost;
it does not print response content, prompts, endpoint IDs, request IDs or any
secret.

The only local secret names reserved for a later separately reviewed live smoke
are `VOLCENGINE_ASR_API_KEY` (Doubao Voice new-console key) and
`VOLCENGINE_ARK_API_KEY` (Ark API key). Neither is loaded by the adapter or
persisted by the smoke entry. A future paid implementation must additionally
receive a reviewed endpoint ID, media URL policy, price catalog and explicit
one-call confirmation.
