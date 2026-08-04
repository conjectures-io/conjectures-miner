# conjectures-miner

The miner CLI for the [conjectures.io](https://conjectures.io) Subnet 66 Lean-proof validator.
Pick a task, build a submission bundle, check it for free, then sign and send it.

```bash
uv tool install conjectures-miner    # or: uv sync && uv run conjectures
```

## The flow

```bash
conjectures tasks sync                       # cache the allowlist; short names + completion
conjectures tasks list --filter erdos

conjectures build --proof Main.lean --task erdos89
conjectures check                            # free, unauthenticated, no key unlock

#   pay 0.5 TAO from your coldkey to the payment_recipient, and wait for finality

conjectures submit --payment-ref <extrinsic>
conjectures submissions show <id> --watch
```

`build` writes two files and is fully offline:

- **`submission.zip`** -- the archive that will be sent, sealed once and never rebuilt.
- **`submission.plan.json`** -- where the archive is, what it must hash to, a readable copy of its
  manifest, and an empty payment slot.

`check` and `submit` both take the plan, verify the archive still hashes to what was sealed, and
work from the archive's own manifest. Nothing about the task is typed twice, and what `check`
approved is literally what `submit` sends.

## What costs money, and what does not

`tasks`, `status`, `build` and `check` are free. Only `submit` spends a payment, and only after
it shows you what is about to be spent (`--yes` skips the prompt).

If the validator refuses a submission, **the payment is not consumed** -- no submission row is
written, so the same extrinsic reference still works. The idempotency key is written to disk
*before* the request goes out, which is what makes a retry safe: reuse it and you get the
original outcome rather than a second charge.

## Configuration

Precedence, highest first: **CLI flag -> environment (`CONJECTURES_*`) -> user config file ->
default.**

```bash
conjectures config path
conjectures config set api_base_url https://<validator-host>
conjectures config show --resolved      # every value, and which layer it came from
```

Wallet *names* live in the config; key material never does. The hotkey signs, and the coldkey
that paid is checked on-chain by the validator -- this tool never needs your coldkey.

`--output json` emits exactly one JSON document on stdout, so `conjectures tasks list --output
json | jq` works. Exit codes: `1` refused, `2` bad configuration or input, `3` the validator said
no, `4` the validator was unreachable.

## Development

```bash
uv sync
uv run pytest
uv run ruff format . && uv run ruff check . && uv run pyright
```

`digest.py` and `bundle.py` are byte-exact contracts with the validator and are pinned by
`tests/vectors/`. Change them only against a known validator commit.
