from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import AuthMethod, Credentials, ServerProfile


class StorageError(Exception):
    pass


def data_directory() -> Path:
    override = os.environ.get("SCPPRO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "ScpPro"
    return Path.home() / "Library/Application Support/ScpPro"


SECRET_STORE_NAME = "Windows 凭据管理器" if sys.platform == "win32" else "macOS 钥匙串"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass
class AppState:
    profiles: list[ServerProfile] = field(default_factory=list)
    local_path: str = field(default_factory=lambda: str(Path.home()))
    remote_paths: dict[str, str] = field(default_factory=dict)
    show_hidden: bool = False


class ProfileStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.path = self.directory / "profiles.json"
        self.known_hosts_path = self.directory / "known_hosts"

    def load(self) -> AppState:
        if not self.path.exists():
            return AppState()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("version") != 1:
                raise ValueError("不支持的配置版本")
            profiles = []
            seen_ids: set[str] = set()
            for row in document["profiles"]:
                profile = ServerProfile(
                    id=str(row["id"]), name=str(row["name"]), host=str(row["host"]),
                    username=str(row["username"]), port=int(row.get("port", 22)),
                    auth_method=AuthMethod(row.get("auth_method", "password")),
                    key_path=str(row.get("key_path", "")),
                    remember_secret=bool(row.get("remember_secret", False)),
                )
                profile.validate()
                if profile.id in seen_ids:
                    raise ValueError("服务器配置标识重复")
                seen_ids.add(profile.id)
                profiles.append(profile)
            remote_paths = document.get("remote_paths", {})
            if not isinstance(remote_paths, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in remote_paths.items()):
                raise ValueError("远程目录配置无效")
            return AppState(
                profiles=profiles, local_path=str(document.get("local_path", Path.home())),
                remote_paths=remote_paths, show_hidden=bool(document.get("show_hidden", False)),
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise StorageError(f"无法读取服务器配置：{self.path}\n{exc}\n原配置已保留，请修复或备份移走后重启应用。") from exc

    def save(self, state: AppState) -> None:
        document = {
            "version": 1,
            "profiles": [asdict(profile) for profile in state.profiles],
            "local_path": state.local_path,
            "remote_paths": state.remote_paths,
            "show_hidden": state.show_hidden,
        }
        try:
            atomic_write(self.path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            raise StorageError(f"无法保存服务器配置：{exc}") from exc

    def save_host_keys(self, keys) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".known-hosts-", dir=self.directory)
        os.close(descriptor)
        try:
            keys.save(temporary)
            os.replace(temporary, self.known_hosts_path)
        finally:
            Path(temporary).unlink(missing_ok=True)


class SecretStore:
    """Use the native OS credential store; never fall back to a file backend."""

    def __init__(self, backend=None, service: str = "ScpPro"):
        if backend is None:
            if sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring

                backend = WinVaultKeyring()
            elif sys.platform == "darwin":
                from keyring.backends.macOS import Keyring

                backend = Keyring()
            else:
                raise RuntimeError("ScpPro 目前仅支持 Windows 和 macOS。")
        self.backend = backend
        self.service = service

    @staticmethod
    def account(profile: ServerProfile) -> str:
        return f"{profile.id}:{profile.auth_method.value}"

    def get(self, profile: ServerProfile) -> Credentials | None:
        secret = self.backend.get_password(self.service, self.account(profile))
        return Credentials(secret) if secret is not None else None

    def set(self, profile: ServerProfile, credentials: Credentials) -> None:
        self.backend.set_password(self.service, self.account(profile), credentials.secret)

    def delete(self, profile: ServerProfile) -> None:
        from keyring.errors import PasswordDeleteError

        for method in AuthMethod:
            try:
                self.backend.delete_password(self.service, f"{profile.id}:{method.value}")
            except PasswordDeleteError:
                pass
