import os
from pathlib import Path
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from scppro.models import Credentials, ServerProfile
from scppro.sftp import Session
from scppro.storage import ProfileStore
from .sftp_server import LocalSFTPServer


@pytest.fixture
def server(tmp_path):
    service = LocalSFTPServer(tmp_path / "remote")
    yield service
    service.close()


@pytest.fixture
def profile(server):
    return ServerProfile("测试服务器", "127.0.0.1", "tester", port=server.port)


@pytest.fixture
def store(tmp_path):
    return ProfileStore(tmp_path / "app-data")


@pytest.fixture
def session(profile, store):
    connection = Session(profile, store)
    connection.connect(Credentials("test-password"), lambda *_args: True, threading.Event())
    yield connection
    connection.close()


class MemoryKeyring:
    def __init__(self):
        self.secrets = {}

    def get_password(self, service, account):
        return self.secrets.get((service, account))

    def set_password(self, service, account, password):
        self.secrets[service, account] = password

    def delete_password(self, service, account):
        from keyring.errors import PasswordDeleteError

        if (service, account) not in self.secrets:
            raise PasswordDeleteError("missing")
        del self.secrets[service, account]


@pytest.fixture
def secret_store():
    from scppro.storage import SecretStore

    return SecretStore(MemoryKeyring(), service="ScpPro.Tests")
