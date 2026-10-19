from dataclasses import asdict, replace
import json
import stat
import threading

import paramiko
import pytest

from scppro.models import AuthMethod, Credentials
from scppro.sftp import HostKeyRejected, Session, fingerprint
from scppro.storage import AppState, StorageError


def test_profiles_round_trip_and_no_secrets(store, profile, secret_store):
    state = AppState([replace(profile, remember_secret=True)], local_path="/tmp", remote_paths={profile.id: "/work"}, show_hidden=True)
    credentials = Credentials("private-test-password")
    secret_store.set(state.profiles[0], credentials)
    store.save(state)
    loaded = store.load()
    assert loaded == state
    assert credentials.secret not in store.path.read_text()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert secret_store.get(profile) == credentials
    secret_store.delete(profile)
    assert secret_store.get(profile) is None


def test_corrupt_config_is_preserved(store):
    store.directory.mkdir()
    original = "broken config content"
    store.path.write_text(original)
    with pytest.raises(StorageError):
        store.load()
    assert store.path.read_text() == original


def test_host_key_first_trust_then_reuse(profile, store):
    confirmations = []
    for _ in range(2):
        session = Session(profile, store)
        session.connect(Credentials("test-password"), lambda *args: (confirmations.append(args) or True), threading.Event())
        assert session.is_connected
        session.close()
    assert len(confirmations) == 1
    assert confirmations[0][2].startswith("SHA256:")
    assert stat.S_IMODE(store.known_hosts_path.stat().st_mode) == 0o600


def test_reject_unknown_host(profile, store):
    session = Session(profile, store)
    with pytest.raises(HostKeyRejected):
        session.connect(Credentials("test-password"), lambda *_args: False, threading.Event())
    assert not session.is_connected
    assert not store.known_hosts_path.exists()


def test_changed_host_key_is_blocked(profile, store, server):
    keys = paramiko.HostKeys()
    keys.add(f"[{profile.host}]:{profile.port}", "ssh-rsa", paramiko.RSAKey.generate(2048))
    store.save_host_keys(keys)
    session = Session(profile, store)
    with pytest.raises(paramiko.BadHostKeyException):
        session.connect(Credentials("test-password"), lambda *_args: pytest.fail("must not ask to accept a changed key"), threading.Event())
    assert not session.is_connected


@pytest.mark.parametrize("encrypted", [False, True])
def test_private_key_authentication(profile, server, store, tmp_path, encrypted):
    key_path = tmp_path / "id_test"
    server.user_key.write_private_key_file(str(key_path), password="private-key-passphrase" if encrypted else None)
    keyed = replace(profile, auth_method=AuthMethod.KEY, key_path=str(key_path))
    session = Session(keyed, store)
    session.connect(Credentials("private-key-passphrase" if encrypted else ""), lambda *_args: True, threading.Event())
    assert session.is_connected
    session.close()


def test_bad_password_does_not_leave_connection(profile, store):
    session = Session(profile, store)
    with pytest.raises(paramiko.AuthenticationException):
        session.connect(Credentials("incorrect"), lambda *_args: True, threading.Event())
    assert not session.is_connected


@pytest.mark.parametrize("encrypted", [False, True])
def test_openssh_ed25519_key_authentication(profile, server, store, tmp_path, encrypted):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_path = tmp_path / "id_ed25519"
    key = Ed25519PrivateKey.generate()
    encryption = serialization.BestAvailableEncryption(b"ed25519-passphrase") if encrypted else serialization.NoEncryption()
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, encryption))
    server.user_key = paramiko.Ed25519Key.from_private_key_file(str(key_path), password="ed25519-passphrase" if encrypted else None)
    keyed = replace(profile, auth_method=AuthMethod.KEY, key_path=str(key_path))
    session = Session(keyed, store)
    session.connect(Credentials("ed25519-passphrase" if encrypted else ""), lambda *_args: True, threading.Event())
    assert session.is_connected
    session.close()
