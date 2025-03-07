import base64
import datetime
import json

import cryptography.fernet
import flask
import flask_socketio
import peewee
from oauthlib.oauth2.rfc6749.errors import InvalidTokenError
from requests_oauthlib import OAuth2Session
import flask_discord.models

from randovania.network_common.error import InvalidSession, NotAuthorizedForAction, InvalidAction, UserNotAuthorized
from randovania.server.database import User, UserAccessToken, GameSessionMembership
from randovania.server.lib import logger
from randovania.server.server_app import ServerApp


def _encrypt_session_for_user(sio: ServerApp, session: dict) -> bytes:
    encrypted_session = sio.fernet_encrypt.encrypt(json.dumps(session).encode("utf-8"))
    return base64.b85encode(encrypted_session)


def _create_client_side_session_raw(sio: ServerApp, user: User) -> dict:
    logger().info(f"Client at {sio.current_client_ip()} is user {user.name} ({user.id}).")

    return {
        "user": user.as_json,
        "sessions": [
            membership.session.create_list_entry()
            for membership in GameSessionMembership.select().where(GameSessionMembership.user == user)
        ],
    }

def _create_client_side_session(sio: ServerApp, user: User | None) -> dict:
    """

    :param user: If the session's user was already retrieved, pass it along to avoid an extra query.
    :return:
    """
    session = sio.get_session()
    if user is None:
        user = User.get_by_id(session["user-id"])
    elif user.id != session["user-id"]:
        raise RuntimeError(f"Provided user does not match the session's user")

    result = _create_client_side_session_raw(sio, user)
    result["encoded_session_b85"] = _encrypt_session_for_user(sio, session)

    return result


def _create_user_from_discord(discord_user: flask_discord.models.User) -> User:
    user: User = User.get_or_create(discord_id=discord_user.id,
                                    defaults={"name": discord_user.name})[0]
    if user.name != discord_user.name:
        user.name = discord_user.name
        user.save()

    return user


def _create_session_with_access_token(sio: ServerApp, token: UserAccessToken) -> bytes:
    return _encrypt_session_for_user(
        sio,
        {
            "user-id": token.user.id,
            "rdv-access-token": token.name,
        }
    )


def _create_session_with_discord_token(sio: ServerApp, access_token: str) -> tuple[User, dict]:
    flask.session["DISCORD_OAUTH2_TOKEN"] = access_token
    discord_user = sio.discord.fetch_user()

    user = _create_user_from_discord(discord_user)

    if sio.enforce_role is not None:
        if not sio.enforce_role.verify_user(discord_user.id):
            logger().info("User %s is not authorized for connecting to the server", discord_user.name)
            raise UserNotAuthorized()

    with sio.session() as session:
        session["user-id"] = user.id
        session["discord-access-token"] = access_token

    return user, _create_client_side_session(sio, user)


def login_with_discord(sio: ServerApp, code: str) -> dict:
    oauth = OAuth2Session(
        client_id=sio.app.config["DISCORD_CLIENT_ID"],
        redirect_uri=sio.app.config["DISCORD_REDIRECT_URI"],
    )
    access_token = oauth.fetch_token(
        "https://discord.com/api/oauth2/token",
        code=code,
        client_secret=sio.app.config["DISCORD_CLIENT_SECRET"],
    )
    return _create_session_with_discord_token(sio, access_token)[1]


def _get_now():
    # For mocking in tests
    return datetime.datetime.now(datetime.timezone.utc)


def login_with_guest(sio: ServerApp, encrypted_login_request: bytes):
    if sio.guest_encrypt is None:
        raise NotAuthorizedForAction()

    try:
        login_request_bytes = sio.guest_encrypt.decrypt(encrypted_login_request)
    except cryptography.fernet.InvalidToken:
        raise NotAuthorizedForAction()

    try:
        login_request = json.loads(login_request_bytes.decode("utf-8"))
        name = login_request["name"]
        date = datetime.datetime.fromisoformat(login_request["date"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError) as e:
        raise InvalidAction(str(e))

    if _get_now() - date > datetime.timedelta(days=1):
        raise NotAuthorizedForAction()

    user: User = User.create(name=f"Guest: {name}")

    with sio.session() as session:
        session["user-id"] = user.id

    return _create_client_side_session(sio, user)


def restore_user_session(sio: ServerApp, encrypted_session: bytes, session_id: int | None):
    try:
        decrypted_session: bytes = sio.fernet_encrypt.decrypt(encrypted_session)
        session = json.loads(decrypted_session.decode("utf-8"))

        if "discord-access-token" in session:
            user, result = _create_session_with_discord_token(sio, session["discord-access-token"])
        else:
            user = User.get_by_id(session["user-id"])
            sio.save_session(session)

            if "rdv-access-token" in session:
                access_token = UserAccessToken.get(
                    user=user,
                    name=session["rdv-access-token"],
                )
                access_token.last_used = datetime.datetime.now(datetime.timezone.utc)
                access_token.save()

                result = _create_client_side_session_raw(sio, user)

            else:
                result = _create_client_side_session(sio, user)

        if session_id is not None:
            sio.join_game_session(GameSessionMembership.get_by_ids(user.id, session_id))

        return result

    except UserNotAuthorized:
        sio.save_session({})
        raise

    except (KeyError, peewee.DoesNotExist, json.JSONDecodeError, InvalidTokenError) as e:
        # InvalidTokenError: discord token expired and couldn't renew
        sio.save_session({})
        logger().info("Client at %s was unable to restore session: (%s) %s",
                      sio.current_client_ip(), str(type(e)), str(e))
        raise InvalidSession()

    except Exception:
        sio.save_session({})
        logger().exception("Error decoding user session")
        raise InvalidSession()


def _emit_user_session_update(sio: ServerApp):
    flask_socketio.emit("user_session_update", _create_client_side_session(sio, None), room=None)


def logout(sio: ServerApp):
    sio.leave_game_session()
    flask.session.pop("DISCORD_OAUTH2_TOKEN", None)
    with sio.session() as session:
        session.pop("discord-access-token", None)
        session.pop("user-id", None)


def setup_app(sio: ServerApp):
    sio.on("login_with_discord", login_with_discord)
    sio.on("login_with_guest", login_with_guest)
    sio.on("restore_user_session", restore_user_session)
    sio.on("logout", logout)

    @sio.app.route("/login")
    def browser_login_with_discord():
        return sio.discord.create_session()

    @sio.app.route("/login_callback")
    def browser_discord_login_callback():
        sio.discord.callback()
        discord_user = sio.discord.fetch_user()

        user = _create_user_from_discord(discord_user)

        return flask.redirect(flask.url_for("browser_me"))

    @sio.route_with_user("/me")
    def browser_me(user: User):
        result = f"Hello {user.name}. Admin? {user.admin}<br />Access Tokens:<ul>\n"

        for token in user.access_tokens:
            delete = f' <a href="{flask.url_for("delete_token", token=token.name)}">Delete</a>'
            result += f"<li>{token.name} created at {token.creation_date}. Last used at {token.last_used}. {delete}</li>"

        result += f'<li><form class="form-inline" method="POST" action="{flask.url_for("create_token")}">'
        result += '<input id="name" placeholder="Access token name" name="name">'
        result += '<button type="submit">Create new</button></li></ul>'

        return result

    @sio.route_with_user("/create_token", methods=["POST"])
    def create_token(user: User):
        token_name: str = flask.request.form['name']
        go_back = f'<a href="{flask.url_for("browser_me")}">Go back</a>'

        try:
            token = UserAccessToken.create(
                user=user,
                name=token_name,
            )
            session = _create_session_with_access_token(sio, token).decode("ascii")
            return f'Token: <pre>{session}</pre><br />{go_back}'

        except peewee.IntegrityError as e:
            return f'Unable to create token: {e}<br />{go_back}'

    @sio.route_with_user("/delete_token")
    def delete_token(user: User):
        token_name: str = flask.request.args['token']
        UserAccessToken.get(
            user=user,
            name=token_name,
        ).delete_instance()
        return flask.redirect(flask.url_for("browser_me"))
