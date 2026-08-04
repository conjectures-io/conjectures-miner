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

conjectures pay                              # 0.5 TAO coldkey -> treasury, recorded on the plan
conjectures submit
conjectures submissions show <id> --watch
```

`pay` reads the treasury address and the exact price from the validator, checks on chain that
your coldkey owns the submitting hotkey — the validator requires that, and it checks *after* the
money has moved — sends the transfer, follows it to finality, and writes the resolved reference
onto the plan. `submit` then needs no `--payment-ref` at all. `--dry-run` runs every check and
sends nothing.

A payment reference is a **position**, `block-extrinsic` or `block-extrinsic-event` — not an
extrinsic hash. A node can resolve a position; resolving a hash is an indexer's job, so the
validator cannot confirm a payment from one. If the extrinsic moved TAO more than once, name the
event index too; `pay` resolves that for you and will tell you which references to choose
between.

Paid outside this tool, or lost the reference before it was recorded? Resolve it from the
position your wallet or a block explorer shows:

```bash
conjectures pay reference --extrinsic 4821993-2 --plan submission.plan.json
```

`build` writes two files and is fully offline:

- **`submission.zip`** -- the archive that will be sent, sealed once and never rebuilt.
- **`submission.plan.json`** -- where the archive is, what it must hash to, a readable copy of its
  manifest, and the payment slot `pay` fills.

`check` and `submit` both take the plan, verify the archive still hashes to what was sealed, and
work from the archive's own manifest. Nothing about the task is typed twice, and what `check`
approved is literally what `submit` sends.

## What costs money, and what does not

`tasks`, `status`, `build`, `check` and `pay reference` are free. `pay` moves TAO on chain and
`submit` spends it; both show you what is about to happen first (`--yes` skips the prompt).

They stay separate commands on purpose. A single command that paid *and* submitted would make
every submission failure look like a lost transfer, and would invite a retry that pays twice.
A plan that already cites a payment refuses a second `pay`, because that reference is the only
local record of money that has moved.

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

Wallet *names* live in the config; key material never does. The hotkey signs every authenticated
request. **`pay` is the one command that opens your coldkey**, because a transfer has to be signed
by the account holding the funds -- it never leaves the process, and what goes on chain is a signed
extrinsic. Every other command runs without it.

`pay` also needs to know which chain to transfer on:

```bash
conjectures config set bittensor_network finney     # or test, local, or a ws:// endpoint
```

Against a validator running outside `APP_MODE=PROD`, `--dev-signature` sends the fixed marker its
static-key authenticator expects instead of signing (`conjectures config set dev_signature true`
to keep it). That mode opens no private key at all -- the marker is a constant -- and a production
validator refuses it, so the default is a real signature.

`--output json` emits exactly one JSON document on stdout, so `conjectures tasks list --output
json | jq` works. Exit codes: `1` refused, `2` bad configuration or input, `3` the validator said
no, `4` the validator or the chain was unreachable.

## Development

```bash
uv sync
uv run pytest
uv run ruff format . && uv run ruff check . && uv run pyright
```

`digest.py` and `bundle.py` are byte-exact contracts with the validator and are pinned by
`tests/vectors/`. Change them only against a known validator commit.
