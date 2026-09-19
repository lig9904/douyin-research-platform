# Volcengine Doubao ASR adapter v1

This adapter maps the official Doubao Voice asynchronous recording-file API to
the provider-neutral ASR execution boundary.

## Verified official contract

Source: [Volcengine recording-file recognition standard HTTP documentation](https://docs.volcengine.com/docs/DoubaoVoice/LargemodelrecordingfilerecognitionstandardversionAPI?lang=zh)

The page was last updated on 2026-06-26 and documents:

- base URL: `https://openspeech.bytedance.com`;
- submit: `POST /api/v3/auc/bigmodel/submit`;
- query: `POST /api/v3/auc/bigmodel/query`;
- new-console authentication header: `X-Api-Key`;
- model 2.0 resource ID: `volc.seedasr.auc`;
- request model name: `bigmodel`;
- stable task reference in `X-Api-Request-Id`;
- `20000000` success, `20000001` processing, `20000002` queued,
  and `20000003` no speech;
- completed response text, utterance start/end milliseconds, and audio duration.

The committed source fingerprint is the SHA-256 of the canonical URL plus the
documented update timestamp. It is audit metadata, not a credential.

## Conservative defaults

The adapter:

- has zero automatic retries;
- derives a stable UUID task reference from the internal task key;
- enables punctuation, ITN, and utterance timestamps;
- disables semantic smoothing, emotion detection, and gender detection;
- sends a fixed non-personal UID;
- accepts only credential-free HTTPS media URLs;
- requires the execution input fingerprint at construction and verifies it again on submit;
- never includes the API key in its contract, representation, database record,
  or sanitized error;
- records actual API/ASR cost as unknown until provider billing data is
  reconciled; explicit LLM cost remains zero.

## Local production-readiness report

`assess_volcengine_asr_readiness(VolcengineASRReadinessFacts(...))` is a pure
local check. It makes no HTTP request, opens no provider client, reads no
environment variable, and accepts no secret value. The sole credential-related
input is `secret_configured: bool`: the operator may attest that a deployment
secret exists without supplying, logging, or persisting it.

The report records these versioned, non-secret facts:

- `price_catalog_version`: the price sheet or catalog revision used for the
  estimate;
- `cost_reconciliation_version`: the documented procedure for reconciling an
  eventual provider bill;
- `polling_policy_version` and `polling_billed`: the source revision and
  explicit billing result for asynchronous query operations.

It also records model enablement and media-delivery verification. Missing facts
and billed polling are explicit blockers. A stable report fingerprint covers
the facts, fixed adapter identity, documentation fingerprint, and blockers, so
a review artifact can be compared without copying credentials.

Even a complete report has `production_ready=false`. It is evidence for an
independent code and operator review, not authorization to execute a paid call.
The runtime constructor has no `production_ready` argument and its execution
contract is permanently fail-closed. Enabling production later requires a
deliberate source-code change and separate review.

The readiness facts to collect are:

1. the configured secret is a Doubao Voice **new-console API Key**, not a
   TikHub key, Ark key, legacy App ID, Access Token, AK, or SK;
2. recording-file recognition model 2.0 is enabled for the account;
3. the source supplied to `media_ref` is a supported audio URL using
   `raw`, `wav`, `mp3`, or `ogg`;
4. the current price and cost-reconciliation method are recorded;
5. query/poll operations are confirmed not to incur an independent charge.

Until a separately reviewed source-code change exists, both the adapter and the
execution coordinator fail before any HTTP request or budget reservation. No
live call is part of the test suite.
