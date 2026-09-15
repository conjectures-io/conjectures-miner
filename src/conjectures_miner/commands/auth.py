"""Register a coldkey account, or exchange its CLI challenge for a scoped bearer token."""

from __future__ import annotations

from typing import Annotated

import typer

from conjectures_miner import session as session_module
from conjectures_miner.api import models
from conjectures_miner.commands import context
from conjectures_miner.context import AppContext
from conjectures_miner.errors import ApiError, CliError, ConfigError, TransportError
from conjectures_miner.session import Session
from conjectures_miner.signing import (
    assert_login_challenge,
    assert_session_challenge,
    bearer_headers,
    challenge_signature,
    coldkey_address,
    load_coldkey,
)

app = typer.Typer(
    help="Claim your conjectures.io account, and sign in to it.", no_args_is_help=True
)


@app.command("register")
def register(
    ctx: typer.Context,
    coldkey_uri: Annotated[
        str | None,
        typer.Option(help="Development coldkey such as //Alice. Local validators only."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Claim an account with your coldkey. Run once."""
    app_ctx = context(ctx)
    settings = app_ctx.settings

    coldkey_uri = coldkey_uri or app_ctx.uri
    coldkey = coldkey_address(settings, uri=coldkey_uri)

    challenge = app_ctx.client.wallet_challenge(address=coldkey)
    # Before the prompt, and before the coldkey is unlocked. This is the guard that matters most
    # in the tool: the same key signs `conjectures-deposit-claim-v1`, so an unchecked signature
    # here could claim a transfer instead of opening a session.
    assert_login_challenge(challenge.message, address=coldkey, api_root=settings.api_root)

    app_ctx.render.preview(
        {
            "coldkey": coldkey,
            "validator": settings.api_root,
            "challenge_expires_at": challenge.expires_at,
        },
        title="about to claim an account",
    )
    app_ctx.render.note("The validator asked your coldkey to sign exactly this:")
    app_ctx.render.log(challenge.message)
    if not yes and not app_ctx.render.confirm("Sign it with your coldkey?"):
        raise typer.Abort

    signer = load_coldkey(settings, uri=coldkey_uri)
    if signer.ss58_address != coldkey:
        raise ConfigError(
            f"the challenge is for {coldkey} but {signer.ss58_address} is signing",
            hint="Point --wallet at the coldkey the challenge was minted for.",
        )

    envelope = app_ctx.client.wallet_verify(
        address=coldkey, signature=challenge_signature(signer, challenge.message)
    )
    try:
        account = envelope.account
        app_ctx.render.data(_account_view(account) | {"coldkey": coldkey}, title="registered")
        app_ctx.render.note(
            f"Account {_name(account)} claimed by {coldkey}. Next: `conjectures auth login`."
        )
    finally:
        _close_browser_session(app_ctx)


def _close_browser_session(app_ctx: AppContext) -> None:
    """Revoke the cookie session server-side, then drop it locally.

    Best-effort on the wire and unconditional locally: if the validator cannot be reached the
    row expires on its own, and there is nothing useful to do about it here -- but the jar is
    emptied either way, and the failure is reported rather than swallowed silently, because a
    live browser session is not a detail to leave unmentioned.
    """
    try:
        app_ctx.client.logout(headers=app_ctx.client.browser_write_headers())
    except (ApiError, CliError) as exc:
        app_ctx.render.note(
            f"[yellow]could not revoke the temporary browser session: {exc}[/] "
            "It was not stored anywhere and the validator will expire it."
        )
    finally:
        app_ctx.client.clear_cookies()


@app.command("login")
def login(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
    show_token: Annotated[
        bool,
        typer.Option("--show-token", help="Include the token in the output, to copy elsewhere."),
    ] = False,
) -> None:
    """Sign a challenge with your coldkey and store the session token it earns."""
    app_ctx = context(ctx)
    settings = app_ctx.settings

    # First, and before any request: the marker is a constant, so it can never verify against a
    # freshly minted nonce. Sending it would burn a challenge to earn a SIGNATURE_INVALID that
    # points at the signature rather than at the flag that caused it.
    if settings.dev_signature:
        raise ConfigError(
            "--dev-signature cannot sign in: it sends a fixed marker, not a signature",
            hint="Drop --dev-signature (or `conjectures config set dev_signature false`). "
            "`--uri //Alice` against a local validator does work -- that is a real keypair.",
        )

    # The public address only. A challenge the validator refuses -- rate limited, coldkey unknown --
    # must cost no passphrase prompt, so the key stays locked until there is something to sign.
    address = app_ctx.coldkey_address()
    challenge = app_ctx.client.cli_challenge(address=address)

    # Before the prompt, and before the key is unlocked. Signing bytes a server chose without
    # checking them would make this command a signing oracle for the four other messages the
    # validator asks this same coldkey to sign -- `conjectures-coldkey-link-v1` above all, a
    # signature over which attaches this coldkey to whichever account asked. See
    # `signing.assert_session_challenge`.
    assert_session_challenge(challenge.message, address=address, api_root=settings.api_root)

    app_ctx.render.preview(
        {"coldkey": address, "challenge_expires_at": challenge.expires_at},
        title="about to sign",
    )
    app_ctx.render.note("The validator asked you to sign exactly this:")
    app_ctx.render.log(challenge.message)
    if not yes and not app_ctx.render.confirm("Sign it with your coldkey?"):
        raise typer.Abort

    signer = app_ctx.signer
    if signer.ss58_address != address:
        raise ConfigError(
            f"the challenge is for {address} but {signer.ss58_address} is signing",
            hint="Point --wallet at the coldkey the challenge was minted for.",
        )

    issued = app_ctx.client.cli_verify(
        address=address,
        # Names which challenge is being answered, so a challenge someone else requested for this
        # same coldkey cannot supersede ours. Not the proof -- the signature is.
        nonce=challenge.nonce,
        # The validator's message, byte for byte. Not rebuilt from the nonce: the validator
        # verifies against the copy it stored, and a rebuild that differs anywhere fails.
        signature=challenge_signature(signer, challenge.message),
    )
    if issued.coldkey_scope != address:
        raise ConfigError("the issued session names a different coldkey; sign in again")
    # Truncates whatever was there, so signing in while already signed in is a replacement rather
    # than a refusal -- and the browser session is untouched either way.
    path = session_module.save(
        Session(
            access_token=issued.access_token,
            expires_at=issued.expires_at,
            api_base_url=settings.api_root,
            coldkey=address,
            account_id=str(issued.account.id),
        )
    )
    # One document, so `--output json | jq` still works: the token joins the result rather than
    # following it. A second `data` call would append a second JSON object to the same stream.
    view = _account_view(issued.account) | {"session_file": str(path)}
    if show_token:
        view |= {"access_token": issued.access_token}
    app_ctx.render.data(view, title="signed in")
    app_ctx.render.note(
        f"Signed in as {_name(issued.account)} (coldkey {address}), "
        f"expires {issued.expires_at.isoformat()}"
    )
    if not show_token:
        app_ctx.render.note(
            "The token is in the session file above. `conjectures auth token` prints it."
        )


@app.command("token")
def token(ctx: typer.Context) -> None:
    """Print the stored session token, and nothing else. For a script or the clipboard."""
    app_ctx = context(ctx)
    # `require`, not `load`: an expired token, or one minted for another validator, is refused
    # rather than printed. Handing out a credential that will be rejected -- or worse, one that
    # belongs to a different deployment and might be pasted into a request to this one -- is not
    # a convenience. Exits 2 with the reason, like `auth status`.
    stored = session_module.require(app_ctx.settings)

    # `plain`, not `data`: the output is the token, so `TOKEN=$(conjectures auth token)` and
    # `conjectures auth token | xclip` both work without unquoting a JSON string.
    app_ctx.render.plain(stored.access_token)
    # Stderr, so it never lands in the pipe -- but said once, because a token on a terminal is a
    # token in scrollback, in the shell history of whatever consumed it, and in any recording of
    # the session. `conjectures auth logout` is the fix if it goes somewhere it should not.
    app_ctx.render.note(
        f"[dim]a bearer token for {stored.coldkey} at {stored.api_base_url}, expires "
        f"{stored.expires_at.isoformat()} -- treat it like a password[/]"
    )


@app.command("logout")
def logout(ctx: typer.Context) -> None:
    """Revoke the session token, then forget it locally."""
    app_ctx = context(ctx)
    stored = session_module.load()
    if stored is None:
        raise ConfigError(
            "not signed in", hint=f"Nothing stored at {session_module.session_file_path()}."
        )

    if not session_module.matches(stored, app_ctx.settings.api_root):
        # Unsendable here, and keeping it would leave no way to be rid of it without pointing
        # --api back. Clearing is always safe: the worst case is a live row the validator expires.
        session_module.clear()
        app_ctx.render.data(
            {"revoked": False, "cleared_locally": True, "minted_for": stored.api_base_url},
            title="signed out",
        )
        app_ctx.render.note(
            f"The token was minted for {stored.api_base_url}, so it was not sent to "
            f"{app_ctx.settings.api_root}. It is gone locally; the validator will expire the row."
        )
        return

    revoked = True
    try:
        app_ctx.client.logout(headers=bearer_headers(stored.access_token))
    except ApiError as exc:
        if exc.status_code != 401:
            raise
        # Already revoked, or already expired. Either way there is nothing left to keep.
        revoked = False
        app_ctx.render.note("The validator had already revoked this token.")
    except TransportError as exc:
        raise TransportError(
            f"{exc.message} -- the local token was kept",
            hint="Run `conjectures auth logout` again when the validator is reachable, so the "
            "token is revoked there and not just forgotten here.",
        ) from exc

    session_module.clear()
    app_ctx.render.data({"revoked": revoked, "cleared_locally": True}, title="signed out")


@app.command("status")
def status(
    ctx: typer.Context,
    local: Annotated[
        bool,
        typer.Option("--local", help="Report the stored session without asking the validator."),
    ] = False,
) -> None:
    """Whether there is a live session, and what it is for. Exits non-zero when not signed in."""
    app_ctx = context(ctx)
    # `require` raises ConfigError -- exit 2 -- for missing, expired, and minted-elsewhere alike.
    stored = session_module.require(app_ctx.settings)
    view = {
        "coldkey": stored.coldkey,
        "account_id": stored.account_id,
        "api_base_url": stored.api_base_url,
        "expires_at": stored.expires_at,
        "session_file": str(session_module.session_file_path()),
    }
    if local:
        app_ctx.render.data(view | {"live": None}, title="session (not checked)")
        return

    try:
        account = app_ctx.client.read_account(headers=bearer_headers(stored.access_token))
    except ApiError as exc:
        if exc.status_code != 401:
            raise
        # A 401 is the validator saying no, but the question this command asks is "am I signed
        # in", and a script gating on it wants one code whether the token is missing, stale or
        # revoked. So it exits 2 like the other two, not 3.
        raise ConfigError(
            "the stored session is no longer valid; it expired or was revoked",
            hint="Run `conjectures auth login`.",
        ) from exc

    app_ctx.render.data(view | _account_view(account) | {"live": True}, title="session")


def _account_view(account: models.Account) -> dict[str, object]:
    """The account, minus anything secret. There is no token field here on purpose."""
    return {
        "account_id": str(account.id),
        "display_name": account.display_name,
        "roles": list(account.roles),
        "linked_coldkeys": [linked.coldkey for linked in account.wallets],
        "submission_coldkey": account.coldkeys.submission_coldkey,
    }


def _name(account: models.Account) -> str:
    return account.display_name or str(account.id)


def resolve(app_ctx: AppContext) -> dict[str, str]:
    """The bearer header for an account-scoped read, or a refusal saying how to get one."""
    return bearer_headers(session_module.require(app_ctx.settings).access_token)
