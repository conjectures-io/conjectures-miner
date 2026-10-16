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

**4. Choose the wallet.**

```bash
conjectures config set wallet_name my-wallet
```

Names only. No key material belongs in the config file, the environment, or the bundle. The
coldkey signs submissions and pays. The validator requires the payment sender to match the
submitting coldkey. A registered hotkey is not required.

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

`check` validates the ZIP, manifest, and static proof policy, including the token limit.
`verify` runs Lean to check whether the proof is correct.

**8. Pay.** `pay` takes the treasury address and exact price from the validator, checks that
your wallet coldkey matches the archive, sends the transfer, follows it to finality, and records
the reference on the plan. `--dry-run` runs the checks and sends nothing.

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

**9. Upload the chat logs.** Every open problem requires the AI chat logs that produced the
proof, uploaded before the proof is submitted and accepted by a reviewer. Export them as a package
following [the agent instructions](https://conjectures.io/trajectory/agent-instructions.md), then
upload it from a CLI session (`conjectures auth register` once, then `conjectures auth login`):

```bash
conjectures logs upload chat-log-package.zip
```

The package must name the task and the exact proof the plan seals; `logs upload` checks that before
sending. It shows the [chat-log terms](https://conjectures.io/trajectory/chat-log-terms-v4.md) and
asks you to agree (`--yes` agrees without the prompt). Submit within 24 hours. Without them `submit`
is refused and the payment is not spent, so you can upload and submit again with the same payment.

If a reviewer finds the logs incomplete they may ask for more instead of rejecting them.
`conjectures logs status --submission <id>` shows the request and its note; upload a new version
within 14 days with `conjectures logs upload <package.zip> --submission <id>`, up to three versions
in all. Each must still be the original chats and show the proof before it was submitted.

**10. Submit.** No `--payment-ref`: the plan already cites the payment. Signs the request and
spends it, after showing what is about to be spent (`--yes` skips the prompt).

```bash
conjectures submit
```

**11. Watch it.** Verification is asynchronous; the report is the verifier's immutable record and
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
conjectures verify                                  # the sealed submission.zip
conjectures verify --proof Main.lean --task erdos89 # that proof instead, built or not
conjectures verify --status                         # what it built, and whether it is still ready
```

A proof gets the timeout published in its task bundle, and the run is quiet until Lean finishes.
Re-running `--setup` on a host that already built once takes a couple of minutes: it moves the
checkouts to the current revision and re-checks readiness rather than rebuilding.

Proof files may be up to 30 MiB (31,457,280 bytes), with a 32 MiB archive limit. Each task's
published `max_submission_bytes` still applies, and the validator also enforces a
5,000,000-token limit. Update the CLI with `git pull --ff-only && ./install.sh`, and run
`conjectures verify --setup` to refresh an older local verifier.

Linux only, x86_64 or aarch64; on Windows, run it inside WSL2. On a stock Debian or Ubuntu host:

```bash
sudo apt install -y git curl ca-certificates python3 python3-venv zstd
```

The checkouts land under your cache directory; `CONJECTURES_VERIFIER_ROOT` moves them and
`CONJECTURES_VERIFIER_REF` chooses which validator revision to build. Local verification runs a
development sandbox rather than the isolation a validator applies to a proof it did not write, so
it answers whether the proof is correct -- not whether the submission will be accepted.

## What costs money, and what does not

`tasks`, `status`, `build`, `verify`, `check`, `auth` and `pay reference` are free. `pay` moves TAO on
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

## Upgrading from manifest schema 1

Update your checkout with `git pull --ff-only`, then run `./install.sh`. Refresh an existing
local verifier with `conjectures verify --setup`.

The ZIP format is still `conjectures-submission/v1`; the manifest **inside** it now uses
`schema_version: 2` and `miner_coldkey`. The separate local plan retains schema 1.
Rebuild old archives and plans with your **coldkey** address:

```bash
conjectures build --proof Main.lean --task erdos89 --coldkey-ss58 YOUR_COLDKEY \
  --output submission-v2.zip --plan submission-v2.plan.json
conjectures check --plan submission-v2.plan.json
```

If you already paid, keep the old plan's `payment.reference`. Do not pay again: submit the
rebuilt plan using `--payment-ref YOUR_EXISTING_REFERENCE`, signed by the coldkey that paid.
Do not just edit the ZIP's schema number: the identity, signing headers, and signed digest also
changed. `--hotkey-ss58` has been replaced by `--coldkey-ss58`.

Old hotkey session files are not reused as coldkey sessions. Sign in again with `auth login`.
Historical submission records remain on disk.

## Your account on the website

`submissions show` and `report` authenticate with your coldkey. `submissions mine` uses a
bearer token to list the account's submissions.

```bash
conjectures auth register    # prove your coldkey and create or reopen its account
conjectures auth login       # sign a coldkey challenge and store a scoped bearer token
conjectures auth status      # check the stored session
conjectures auth token       # print its token for a script or clipboard
conjectures auth logout      # revoke the token and remove the local copy
conjectures submissions mine
```

Registration and login both use the wallet's **coldkey**. Registration no longer links a
hotkey. It opens a temporary browser session and revokes that session before returning;
login stores a bearer token for subsequent account reads. Configure payout destinations
and the designated submission coldkey on the website.

Both commands check the challenge prefix, address, and validator domain before unlocking the
key. They show the exact message and ask before signing (`--yes` skips confirmation).

The token lives beside `config.toml` in `session.json`, with mode `0600`. It is bound to the
validator that issued it and is not read from the environment. `auth token` prints it to stdout;
`auth login --show-token` includes it in JSON output. Treat a copied token like a password.

`--dev-signature` cannot register or log in: a constant marker cannot prove a fresh challenge.
For a local validator, `--uri //Alice` uses a real development coldkey. Registration also accepts
`--coldkey-uri //Bob`. Development keys are refused against remote validators.

The extrinsic-funded `build`, `check`, `pay`, and `submit` path does not require a session.

## Configuration

Precedence, highest first: **CLI flag -> environment (`CONJECTURES_*`) -> user config file ->
default.**

```bash
conjectures config path
conjectures config show --resolved     # every value, and which layer it came from
```

Wallet names live in config; private keys and session tokens do not. The coldkey signs
submissions, status/report reads, account challenges, and payment transfers. `build` and
`check` need only the public coldkey address. The legacy `wallet_hotkey` setting and `--hotkey`
flag remain readable for existing configurations; they do not select a signing key.

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
