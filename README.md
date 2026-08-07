# conjectures-miner

The miner CLI for the [conjectures.io](https://conjectures.io) Subnet 66 Lean-proof validator.
Pick a task, build a submission bundle, verify and check it for free, then sign and send it.

## Install

```bash
./install.sh
```

With `uv` it installs as a tool; without it, into a private virtualenv linked from
`~/.local/bin` -- so Python 3.12 or newer is the only requirement. Either way it finishes by
installing Tab completion for your shell. `PREFIX=/somewhere ./install.sh` puts it elsewhere.

## Your first submission

**1. Point it at a validator.**

```bash
conjectures status                    # accepting work? queues? banner?
conjectures tasks sync                # cache the allowlist; short names + completion
```

**2. Pick a task.** `list` and `show` read the cache, so they are instant and work offline. A
unique prefix or substring stands in for the full task id everywhere, including completion.

```bash
conjectures tasks list --filter erdos
conjectures tasks show erdos89
```

**3. Write the proof.** `challenge` saves the statement to `challenges/<task_id>/Challenge.lean`
(`--dir` puts it elsewhere), and prints what you are proving -- in particular `task_mode`, since a
`counterexample` task wants the negation. Those are the same bytes that are hashed into the
published `task_bundle_sha256`.

```bash
conjectures tasks challenge erdos89
```

Your `Main.lean` holds the declarations only. It is inserted between a trusted header and footer
that supply the imports and the `namespace`, so an `import` line of your own is a refusal, not a
duplicate:

```lean
theorem target : ¬ (fcTypeOfName% "Erdos89.erdos_89") := by
  ...
```

Also refused: `sorry`, `admit`, `axiom`, `set_option`, `native_decide`, `instance`, attributes,
`macro`/`syntax`/`notation`, and any reference to the source theorem.

**4. Choose the keys.**

```bash
conjectures config set wallet_name my-wallet
conjectures config set wallet_hotkey my-hotkey
```

Names only. No key material belongs in the config file, the environment, or the bundle. The
hotkey signs; the coldkey of the same wallet pays, and the validator checks on-chain that it owns
the hotkey.

**5. Build.** Offline, and writes two files: `submission.zip`, sealed once and never rebuilt, and
`submission.plan.json` -- where the archive is, what it must still hash to, a readable copy of its
manifest, and the payment slot `pay` fills.

```bash
conjectures build --proof Main.lean --task erdos89
```

**6. Verify.** The proof itself, against the validator's own verifier on your machine. Needs
[a verifier of your own](#a-verifier-of-your-own) built once. With no arguments it checks the
`Main.lean` sealed into `submission.zip` -- what `submit` would actually send.

```bash
conjectures verify
```

**7. Check.** Free, unauthenticated, unlocks no key. It exits non-zero on a refusal, so
`conjectures check && conjectures pay` is safe to write.

```bash
conjectures check
```

`check` is a question about the envelope and never reads the proof: it is the zip, the manifest and
the static policy scan. Only `verify` answers whether the proof is correct.

**8. Pay.** `pay` takes the treasury address and the exact price from the validator, asks the
chain whether your coldkey owns the submitting hotkey -- the validator requires that, and it only
checks *after* the money has moved -- sends the transfer, follows it to finality, and records the
resolved reference on the plan. `--dry-run` runs every check and sends nothing.

```bash
conjectures pay --dry-run
conjectures pay
```

A payment reference is a **position**, `block-extrinsic` or `block-extrinsic-event` -- not an
extrinsic hash. A node can resolve a position; resolving a hash is an indexer's job, so the
validator cannot confirm a payment from one. If the extrinsic moved TAO more than once, the event
index is what names one payment; `pay` resolves that for you.

Paid outside this tool, or lost the reference before it was recorded? Resolve it from the position
your wallet or a block explorer shows:

```bash
conjectures pay reference --extrinsic 4821993-2 --plan submission.plan.json
```

**9. Submit.** No `--payment-ref`: the plan already cites the payment. Signs the request and
spends it, after showing what is about to be spent (`--yes` skips the prompt).

```bash
conjectures submit
```

**10. Watch it.** Verification is asynchronous; the report is the verifier's immutable record and
appears once it finishes.

```bash
conjectures submissions show <id> --watch
conjectures submissions report <id>
```

## A verifier of your own

`check` asks the validator whether the envelope is acceptable. Whether the *proof* is correct is a
different question, and the only thing that answers it is the verifier itself. `verify --setup`
builds the validator's own, from source, on your machine.

```bash
conjectures verify --setup                          # first run: ~5 GB down, ~20 GB, 30-60 minutes
conjectures verify                                  # the sealed submission.zip. up to an hour
conjectures verify --proof Main.lean --task erdos89 # that proof instead, built or not
conjectures verify --status                         # what it built, and whether it is still ready
```

A proof gets the same hour the validator allows it, and the run is quiet until Lean finishes.
Re-running `--setup` on a host that already built once takes a couple of minutes: it moves the
checkouts to the current revision and re-checks readiness rather than rebuilding.

Linux only, x86_64 or aarch64; on Windows, run it inside WSL2. On a stock Debian or Ubuntu host:

```bash
sudo apt install -y git curl ca-certificates python3 python3-venv zstd
```

The checkouts land under your cache directory; `CONJECTURES_VERIFIER_ROOT` moves them and
`CONJECTURES_VERIFIER_REF` chooses which validator revision to build. Local verification runs a
development sandbox rather than the isolation a validator applies to a proof it did not write, so
it answers whether the proof is correct -- not whether the submission will be accepted.

## What costs money, and what does not

`tasks`, `status`, `build`, `verify`, `check` and `pay reference` are free. `pay` moves TAO on
chain and `submit` spends it; both show you what is about to happen first (`--yes` skips the
prompt).

They stay separate commands on purpose. A single command that paid *and* submitted would make
every submission failure look like a lost transfer, and would invite a retry that pays twice. A
plan that already cites a payment refuses a second `pay`, because that reference is the only local
record of money that has moved.

If the validator refuses a submission, **the payment is not consumed** -- no submission row is
written, so the same reference still works. The idempotency key is written to disk *before* the
request goes out, which is what makes a retry safe: reuse it and you get the original outcome
rather than a second charge. Every refusal prints whether the payment survived it.

## Configuration

Precedence, highest first: **CLI flag -> environment (`CONJECTURES_*`) -> user config file ->
default.**

```bash
conjectures config path
conjectures config show --resolved     # every value, and which layer it came from
```

Wallet *names* live in the config; key material never does. The hotkey signs every authenticated
request. **`pay` is the one command that opens your coldkey**, because a transfer has to be signed
by the account holding the funds -- it never leaves the process, and what goes on chain is a signed
extrinsic. Every other command runs without it.

Against a validator running outside `APP_MODE=PROD`, `--dev-signature` sends the fixed marker its
static-key authenticator expects instead of signing (`conjectures config set dev_signature true`
to keep it). That mode opens no private key at all -- the marker is a constant -- and a production
validator refuses it, so the default is a real signature.

`--output json` emits exactly one JSON document on stdout. It is a global option, so it goes before
the subcommand: `conjectures --output json tasks list | jq`. Exit codes: `1` refused, `2` bad
configuration or input, `3` the validator said no, `4` the validator or the chain was unreachable,
`5` the local verifier is missing or unfit. For `verify`, `0` is the verifier accepting the proof
and `1` is it rejecting one; every other code means no verdict was reached, so
`verify && check` never mistakes a broken host or a retired task for a wrong proof.

## Development

```bash
uv sync
uv run pytest
uv run ruff format . && uv run ruff check . && uv run pyright
```

`digest.py` and `bundle.py` are byte-exact contracts with the validator and are pinned by
`tests/vectors/`. Change them only against a known validator commit.
