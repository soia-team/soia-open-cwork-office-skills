from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

OVERRIDE_CONFIG_NAME = "SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE"
OVERRIDE_ENV_NAME = "SOIA_MEDIA_PUBLISH_ENV_FILE"
LEGACY_OVERRIDE_CONFIG_NAME = "SOIA_PKM_PUBLISH_WECHAT_DRAFT_CONFIG_FILE"
LEGACY_OVERRIDE_ENV_NAME = "SOIA_PKM_PUBLISH_ENV_FILE"
DEFAULT_CONFIG_FILE = "~/.config/soia-skills/soia-media-publish-wechat-draft/config.yml"
LEGACY_DEFAULT_CONFIG_FILE = (
    "~/.config/soia-skills/soia-open-skills/soia-pkm/"
    "soia-pkm-publish-wechat-draft/config.yml"
)
LEGACY_FLAT_PKM_CONFIG_FILE = "~/.config/soia-skills/soia-pkm-publish-wechat-draft/config.yml"

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
PATH_LIKE_KEYS = {
    "OBSIDIAN_VAULT",
}

_WARNED_LEGACY_PATHS: set[Path] = set()


def _config_home_default() -> Path:
    custom_root = os.environ.get("SOIA_SKILLS_CONFIG_HOME")
    if custom_root:
        return Path(custom_root).expanduser() / "soia-media-publish-wechat-draft" / "config.yml"
    xdg_root = os.environ.get("XDG_CONFIG_HOME")
    if xdg_root:
        return Path(xdg_root).expanduser() / "soia-skills" / "soia-media-publish-wechat-draft" / "config.yml"
    return Path(DEFAULT_CONFIG_FILE).expanduser()


def _candidate_paths() -> list[tuple[Path, bool]]:
    """Return (path, is_legacy) pairs in priority order."""
    paths: list[tuple[Path, bool]] = []
    for env_name in (
        OVERRIDE_CONFIG_NAME,
        OVERRIDE_ENV_NAME,
        LEGACY_OVERRIDE_CONFIG_NAME,
        LEGACY_OVERRIDE_ENV_NAME,
    ):
        configured = os.environ.get(env_name)
        if configured:
            paths.append((Path(configured).expanduser(), False))
    paths.append((_config_home_default(), False))
    if not os.environ.get("SOIA_SKILLS_CONFIG_HOME"):
        paths.append((Path(LEGACY_FLAT_PKM_CONFIG_FILE).expanduser(), True))
        paths.append((Path(LEGACY_DEFAULT_CONFIG_FILE).expanduser(), True))
    return paths


def resolve_explicit_config_path(config_file: str) -> Path:
    """Resolve --config path: supports full path or filename inside the skill config dir."""
    raw = Path(config_file).expanduser()
    if raw.is_file():
        return raw
    if not raw.is_absolute() and len(raw.parts) == 1:
        base_dir = _config_home_default().parent
        for candidate in (base_dir / raw.name, base_dir / "accounts" / raw.name):
            if candidate.is_file():
                return candidate
    raise ValueError(f"找不到指定的配置文件：{config_file}")


def find_config_path(config_file: str | None = None) -> Path | None:
    """Find the active private config.yml, emitting a redacted hint on v1 fallback."""
    if config_file:
        return resolve_explicit_config_path(config_file)
    v2_target = _config_home_default()
    for path, is_legacy in _candidate_paths():
        if not path.is_file():
            continue
        if is_legacy and path not in _WARNED_LEGACY_PATHS:
            _WARNED_LEGACY_PATHS.add(path)
            print(
                f"ℹ️ 检测到旧版配置位置，建议按 DATA_STORAGE_SPEC 迁移至新路径："
                f"mkdir -p {shlex.quote(str(v2_target.parent))} && mv {shlex.quote(str(path))} {shlex.quote(str(v2_target))}",
                file=sys.stderr,
            )
        return path
    return None


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if not value or value in {"null", "~"}:
        return ""
    if value[0] in {"'", '"'} and value[-1:] == value[0]:
        inner = value[1:-1]
        if value[0] == "'" or "\\" not in inner:
            return inner
        try:
            # 只在含反斜杠时展开 \n/\t 等转义；latin-1+backslashreplace 往返
            # 保住非 ASCII 字符（直接 utf-8→unicode_escape 会把中文毁成 mojibake）
            return inner.encode("latin-1", "backslashreplace").decode("unicode_escape")
        except UnicodeDecodeError:
            return inner
    return value.split(" #", 1)[0].strip()


def parse_config_file(path: Path) -> dict[str, object]:
    """Parse minimal YAML subset (`env:`, `default_account:`, `accounts:`, or top-level keys)."""
    env: dict[str, str] = {}
    accounts: dict[str, dict[str, str]] = {}
    default_account: str | None = None
    top_meta: dict[str, str] = {}

    section: str | None = None
    current_alias: str | None = None
    alias_indent: int | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))

        if indent == 0:
            current_alias = None
            alias_indent = None
            if stripped == "env:":
                section = "env"
                continue
            if stripped == "accounts:":
                section = "accounts"
                continue
            section = None
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                k = key.strip()
                parsed = _parse_scalar(value)
                if k == "default_account":
                    default_account = parsed or None
                elif KEY_RE.match(k):
                    if k in PATH_LIKE_KEYS:
                        parsed = os.path.expandvars(os.path.expanduser(parsed))
                    top_meta[k] = parsed
            continue

        if section == "env" and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            if not KEY_RE.match(key):
                continue
            parsed = _parse_scalar(value)
            if key in PATH_LIKE_KEYS:
                parsed = os.path.expandvars(os.path.expanduser(parsed))
            env[key] = parsed
            continue

        if section == "accounts" and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            val_stripped = value.strip()
            if current_alias is None or (alias_indent is not None and indent <= alias_indent):
                if (val_stripped == "" or val_stripped.startswith("#")) and ALIAS_RE.match(key):
                    current_alias = key
                    alias_indent = indent
                    accounts.setdefault(current_alias, {})
                continue
            if current_alias is not None and KEY_RE.match(key):
                accounts[current_alias][key] = _parse_scalar(value)

    for k, v in top_meta.items():
        env.setdefault(k, v)

    return {
        "env": env,
        "default_account": default_account,
        "accounts": accounts,
        "meta": top_meta,
    }


def _load_config_env(path: Path) -> dict[str, str]:
    parsed = parse_config_file(path)
    return dict(parsed["env"])  # type: ignore[arg-type]


def load_private_env(required: bool = False, config_file: str | None = None) -> Path | None:
    """Load private config env values without overriding the process environment.

    Secrets must stay outside the vault and outside the open-source skill repo.
    """
    path = find_config_path(config_file=config_file)
    if path is not None:
        try:
            for key, value in _load_config_env(path).items():
                os.environ.setdefault(key, value)
            return path
        except OSError as exc:
            print(f"⚠️ Could not read private config.yml: {exc}", file=sys.stderr)
    if required:
        checked = ", ".join(str(p) for p, _ in _candidate_paths())
        raise SystemExit(f"Missing private config file. Checked: {checked}")
    return None


def _is_placeholder(value: str) -> bool:
    v = value.strip()
    return not v or (v.startswith("<") and v.endswith(">")) or v.startswith("YOUR_")


def _extract_account_creds(profile: dict[str, str]) -> tuple[str, str]:
    app_id = (profile.get("app_id") or profile.get("WECHAT_APP_ID") or "").strip()
    app_secret = (profile.get("app_secret") or profile.get("WECHAT_APP_SECRET") or "").strip()
    if _is_placeholder(app_id):
        app_id = ""
    if _is_placeholder(app_secret):
        app_secret = ""
    return app_id, app_secret


def _is_example_file(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".example.yml") or lower == "example.yml" or lower == "config.example.yml"


def _discover_sidecar_account_files(config_dir: Path) -> dict[str, tuple[Path, dict[str, str]]]:
    """Discover per-account YAML files: `config.<alias>.yml`, `config-<alias>.yml`, `accounts/<alias>.yml`."""
    discovered: dict[str, tuple[Path, dict[str, str]]] = {}
    if not config_dir.is_dir():
        return discovered

    candidates: list[tuple[str, Path]] = []
    for child in sorted(config_dir.iterdir(), key=lambda p: p.name):
        if not child.is_file() or child.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if child.name == "config.yml" or _is_example_file(child.name):
            continue
        stem = child.stem
        alias: str | None = None
        if stem.startswith("config.") and len(stem) > len("config."):
            alias = stem[len("config."):]
        elif stem.startswith("config-") and len(stem) > len("config-"):
            alias = stem[len("config-"):]
        if alias and ALIAS_RE.match(alias):
            candidates.append((alias, child))

    accounts_dir = config_dir / "accounts"
    if accounts_dir.is_dir():
        for child in sorted(accounts_dir.iterdir(), key=lambda p: p.name):
            if not child.is_file() or child.suffix.lower() not in {".yml", ".yaml"} or _is_example_file(child.name):
                continue
            alias = child.stem
            if ALIAS_RE.match(alias):
                candidates.append((alias, child))

    for alias, file_path in candidates:
        parsed = parse_config_file(file_path)
        env_map: dict[str, str] = parsed["env"]  # type: ignore[assignment]
        profile = dict(env_map)
        accounts_map: dict[str, dict[str, str]] = parsed["accounts"]  # type: ignore[assignment]
        if alias in accounts_map:
            profile.update(accounts_map[alias])
        elif len(accounts_map) == 1:
            profile.update(next(iter(accounts_map.values())))
        discovered[alias] = (file_path, profile)

    return discovered


def _collect_all_accounts(
    config_path: Path | None,
    *,
    include_sidecars: bool = True,
) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, str], str | None]:
    """Collect merged accounts map, source labels, primary env map, and default_account."""
    env_map: dict[str, str] = {}
    accounts: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    default_account: str | None = None

    if config_path is not None and config_path.is_file():
        parsed = parse_config_file(config_path)
        env_map = dict(parsed["env"])  # type: ignore[arg-type]
        default_account = parsed["default_account"]  # type: ignore[assignment]
        inline_accounts: dict[str, dict[str, str]] = parsed["accounts"]  # type: ignore[assignment]
        for alias, profile in inline_accounts.items():
            accounts[alias] = dict(profile)
            sources[alias] = f"{config_path.name}#accounts"

    config_dirs: list[Path] = []
    v2_dir = _config_home_default().parent
    config_dirs.append(v2_dir)
    if config_path is not None and config_path.parent != v2_dir:
        config_dirs.append(config_path.parent)

    if include_sidecars:
        for scan_dir in config_dirs:
            for alias, (side_path, side_profile) in _discover_sidecar_account_files(scan_dir).items():
                if alias in accounts:
                    inline_id, _ = _extract_account_creds(accounts[alias])
                    side_id, _ = _extract_account_creds(side_profile)
                    if inline_id and side_id and inline_id != side_id:
                        raise ValueError(
                            f"账号 {alias!r} 同时存在于 {sources[alias]} 和 {side_path.name} 且 app_id 冲突，请保留一处。"
                        )
                merged = dict(accounts.get(alias, {}))
                merged.update(side_profile)
                accounts[alias] = merged
                sources[alias] = side_path.name

    return accounts, sources, env_map, default_account


def list_accounts(config_path: Path | None = None, config_file: str | None = None) -> list[dict[str, object]]:
    """Return non-secret metadata for configured accounts (inline and per-account yml files)."""
    explicit = config_path is not None or bool(config_file)
    path = config_path or find_config_path(config_file=config_file)
    accounts, sources, _, default_account = _collect_all_accounts(path, include_sidecars=not explicit)
    result: list[dict[str, object]] = []
    for alias, profile in accounts.items():
        app_id, app_secret = _extract_account_creds(profile)
        result.append(
            {
                "alias": alias,
                "name": profile.get("name") or profile.get("WECHAT_ACCOUNT_NAME", ""),
                "wechat_id": profile.get("wechat_id", ""),
                "source": sources.get(alias, ""),
                "is_default": alias == default_account,
                "configured": bool(app_id and app_secret),
            }
        )
    return result


def _match_account(accounts: dict[str, dict[str, str]], selector: str) -> str | None:
    if selector in accounts:
        return selector
    needle = selector.strip().lower()
    matches = []
    for alias, profile in accounts.items():
        candidates = {
            alias.lower(),
            profile.get("wechat_id", "").strip().lower(),
            (profile.get("name") or profile.get("WECHAT_ACCOUNT_NAME", "")).strip().lower(),
        }
        if needle in candidates:
            matches.append(alias)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"账号标识 {selector!r} 命中多个配置（{', '.join(matches)}），请改用精确 alias。")
    return None


def resolve_credentials(
    account: str | None = None,
    config_path: Path | None = None,
    config_file: str | None = None,
) -> tuple[str, str, str | None]:
    """Resolve (app_id, app_secret, resolved_account_alias) fail-closed.

    Supports both multi-account `accounts:` in `config.yml` and per-account
    files (`config.<alias>.yml` / `accounts/<alias>.yml` / `--config`).
    """
    explicit = config_path is not None or bool(config_file)
    path = config_path or find_config_path(config_file=config_file)
    accounts, _, env_map, default_account = _collect_all_accounts(path, include_sidecars=not explicit)

    if account:
        matched_alias = _match_account(accounts, account) if accounts else None
        if matched_alias is not None:
            app_id, app_secret = _extract_account_creds(accounts[matched_alias])
            if not (app_id and app_secret):
                raise ValueError(f"账号 {matched_alias!r} 缺少 app_id / app_secret（或仍为占位符）。")
            return app_id, app_secret, matched_alias

        # 支持扁平 env 约定：WECHAT_APP_ID_<ALIAS> / WECHAT_APP_SECRET_<ALIAS>
        suffix = re.sub(r"[^A-Za-z0-9]+", "_", account.strip()).upper()
        id_key = f"WECHAT_APP_ID_{suffix}"
        sec_key = f"WECHAT_APP_SECRET_{suffix}"
        app_id = os.environ.get(id_key) or env_map.get(id_key, "")
        app_secret = os.environ.get(sec_key) or env_map.get(sec_key, "")
        if not _is_placeholder(app_id) and not _is_placeholder(app_secret):
            return app_id, app_secret, account

        available = ", ".join(accounts.keys()) if accounts else "无"
        raise ValueError(f"未找到指定公众号账号 {account!r}（已配置 accounts: {available}）。")

    if explicit and path is not None and not accounts:
        app_id, app_secret = _extract_account_creds(env_map)
        if not (app_id and app_secret):
            raise ValueError(f"指定配置文件 {path.name} 缺少有效的 WECHAT_APP_ID / WECHAT_APP_SECRET。")
        return app_id, app_secret, path.stem

    if accounts:
        if default_account:
            matched_default = _match_account(accounts, default_account)
            if matched_default is None:
                raise ValueError(
                    f"default_account {default_account!r} 不在 accounts 列表中（可选: {', '.join(accounts.keys())}）。"
                )
            app_id, app_secret = _extract_account_creds(accounts[matched_default])
            if not (app_id and app_secret):
                raise ValueError(f"默认账号 {matched_default!r} 缺少 app_id / app_secret（或仍为占位符）。")
            return app_id, app_secret, matched_default
        if len(accounts) == 1:
            sole_alias = next(iter(accounts))
            app_id, app_secret = _extract_account_creds(accounts[sole_alias])
            if not (app_id and app_secret):
                raise ValueError(f"账号 {sole_alias!r} 缺少 app_id / app_secret（或仍为占位符）。")
            return app_id, app_secret, sole_alias
        raise ValueError(
            f"检测到多个公众号账号（{', '.join(accounts.keys())}），但未指定 --account / --config 且未设置 default_account。"
        )

    app_id = (os.environ.get("WECHAT_APP_ID") or env_map.get("WECHAT_APP_ID", "")).strip()
    app_secret = (os.environ.get("WECHAT_APP_SECRET") or env_map.get("WECHAT_APP_SECRET", "")).strip()
    if _is_placeholder(app_id) or _is_placeholder(app_secret):
        raise ValueError(
            "缺 WECHAT_APP_ID / WECHAT_APP_SECRET：请在私有 config.yml（env / accounts / config.<alias>.yml）或进程环境中配置。"
        )
    return app_id, app_secret, None


def env_source_hint() -> str:
    return f"{OVERRIDE_CONFIG_NAME}, {OVERRIDE_ENV_NAME}, or {DEFAULT_CONFIG_FILE}"


def main() -> int:
    path = find_config_path()
    if not path:
        return 1
    for key, value in _load_config_env(path).items():
        print(f"export {key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
