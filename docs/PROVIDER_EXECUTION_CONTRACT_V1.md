# Provider execution contract v1

Paid ASR and L3 adapters must expose a `VerifiedExecutionContract` before the
coordinator reserves budget or makes an external call. The contract is
secret-free: it records the authentication scheme and header name, never the
credential value.

## Required verification

An operator must verify the following against provider or relay documentation:

- HTTPS base URL and submit/status paths;
- authentication scheme and header name;
- model identifier and immutable revision;
- request and response schema versions;
- currency and whether polling is billed;
- timeout and retry behavior;
- a SHA-256 fingerprint of the exact source material used for verification.

Set `production_ready=True` only after every field is checked. This flag is an
operator assertion, not proof that an external service is correct. The runtime
still validates every field, requires zero SDK retries, and hashes the complete
non-secret contract for audit metadata.

ASR is asynchronous and must define a status path. The current budget model
charges one request for submission and one request per poll at zero estimated
cost, so a contract declaring billed polling is rejected. L3 is synchronous and
must not define a status path.

## Secret handling

Do not put API keys, bearer tokens, cookies, signed URLs, account identifiers,
or request identifiers in the contract, repository, tests, logs, or job
metadata. Credentials remain in the deployment secret store and are read only
by the concrete adapter at call time.

Until the real provider/relay contract has been verified, adapters must either
omit the contract or set `production_ready=False`; both states fail closed
before budget reservation and before any external call.
