#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""
Codex PreToolUse dependency freshness checker.

This hook inspects Codex apply_patch payloads before the patch is applied. It
reconstructs the proposed dependency file contents in memory, then blocks
dependency changes that either:

- reference a package version published less than MIN_PACKAGE_AGE_DAYS ago, or
- lag behind the newest registry version that is at least MIN_PACKAGE_AGE_DAYS old.
"""

from __future__ import annotations

import json
import importlib.util
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover - uv should run us on Python 3.11+.
    tomllib = None  # type: ignore[assignment]


MIN_PACKAGE_AGE_DAYS = int(os.environ.get("CODEX_HOOK_MIN_PACKAGE_AGE_DAYS", "7"))
REGISTRY_TIMEOUT_SECONDS = float(os.environ.get("CODEX_HOOK_REGISTRY_TIMEOUT_SECONDS", "10"))
USER_AGENT = "codex-hooks-dependency-checker/0.1"


@dataclass(frozen=True)
class PatchedFile:
    path: str
    content: str | None
    original_content: str | None = None


@dataclass(frozen=True)
class Dependency:
    manager: str
    file_path: str
    name: str
    spec: str
    version: str
    dep_type: str


@dataclass(frozen=True)
class VersionInfo:
    version: str
    published_at: datetime | None


@dataclass(frozen=True)
class RegistryResult:
    requested: VersionInfo | None
    newest_allowed: VersionInfo | None


@dataclass(frozen=True)
class Vulnerability:
    vuln_id: str
    summary: str


@dataclass(frozen=True)
class DependencyIssue:
    dependency: Dependency
    reason: str
    requested: VersionInfo | None
    newest_allowed: VersionInfo | None
    vulnerabilities: tuple[Vulnerability, ...] = ()


class PatchApplyError(RuntimeError):
    pass


OSV_ECOSYSTEMS = {
    "npm": "npm",
    "pypi": "PyPI",
    "cargo": "crates.io",
    "go": "Go",
}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def version_key(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", version)
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts[:4])


def is_stable_version(version: str) -> bool:
    return re.match(r"^v?\d+\.\d+\.\d+(?:\+incompatible)?$", version) is not None


def is_newer(left: str, right: str) -> bool:
    return version_key(left) > version_key(right)


def extract_base_version(spec: str) -> str | None:
    cleaned = spec.strip().strip('"').strip("'")
    for prefix in ("workspace:", "npm:", "cargo:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    if cleaned.startswith(("file:", "link:", "git:", "git+", "http://", "https://", "path:")):
        return None
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][A-Za-z0-9_.-]+)?", cleaned)
    if not match:
        return None
    major = match.group(1)
    minor = match.group(2) or "0"
    patch = match.group(3) or "0"
    return f"{major}.{minor}.{patch}"


def read_json_url(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REGISTRY_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def read_text_url(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REGISTRY_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def post_json_url(url: str, payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REGISTRY_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def encode_go_module_path(module_path: str) -> str:
    escaped = []
    for char in module_path:
        if "A" <= char <= "Z":
            escaped.append("!" + char.lower())
        else:
            escaped.append(char)
    return urllib.parse.quote("".join(escaped), safe="/!")


class RegistryResolver:
    def resolve(self, dependency: Dependency, cutoff: datetime) -> RegistryResult:
        if dependency.manager == "npm":
            return self._resolve_npm(dependency, cutoff)
        if dependency.manager == "pypi":
            return self._resolve_pypi(dependency, cutoff)
        if dependency.manager == "cargo":
            return self._resolve_cargo(dependency, cutoff)
        if dependency.manager == "go":
            return self._resolve_go(dependency, cutoff)
        return RegistryResult(requested=None, newest_allowed=None)

    def _resolve_npm(self, dependency: Dependency, cutoff: datetime) -> RegistryResult:
        quoted = urllib.parse.quote(dependency.name, safe="")
        data = read_json_url(f"https://registry.npmjs.org/{quoted}")
        times = data.get("time", {})
        versions = []
        requested = None

        for version, published_raw in times.items():
            if version in {"created", "modified"}:
                continue
            published_at = parse_datetime(published_raw)
            info = VersionInfo(version=version, published_at=published_at)
            if version == dependency.version:
                requested = info
            if published_at and published_at <= cutoff and is_stable_version(version):
                versions.append(info)

        newest_allowed = max(versions, key=lambda item: version_key(item.version), default=None)
        return RegistryResult(requested=requested, newest_allowed=newest_allowed)

    def _resolve_go(self, dependency: Dependency, cutoff: datetime) -> RegistryResult:
        module = encode_go_module_path(dependency.name)
        requested = None
        versions = []

        requested_info = self._read_go_version_info(module, dependency.version)
        if requested_info:
            requested = requested_info
            if requested_info.published_at and requested_info.published_at <= cutoff and is_stable_version(requested_info.version):
                versions.append(requested_info)

        version_list = read_text_url(f"https://proxy.golang.org/{module}/@v/list")
        for version in version_list.splitlines():
            version = version.strip()
            if not version or not is_stable_version(version):
                continue
            if requested and version == requested.version:
                continue
            info = self._read_go_version_info(module, version)
            if info and info.published_at and info.published_at <= cutoff:
                versions.append(info)

        newest_allowed = max(versions, key=lambda item: version_key(item.version), default=None)
        return RegistryResult(requested=requested, newest_allowed=newest_allowed)

    def _read_go_version_info(self, encoded_module: str, version: str) -> VersionInfo | None:
        quoted_version = urllib.parse.quote(version, safe="")
        data = read_json_url(f"https://proxy.golang.org/{encoded_module}/@v/{quoted_version}.info")
        resolved_version = str(data.get("Version") or version)
        published_at = parse_datetime(data.get("Time"))
        return VersionInfo(version=resolved_version, published_at=published_at)

    def _resolve_pypi(self, dependency: Dependency, cutoff: datetime) -> RegistryResult:
        quoted = urllib.parse.quote(dependency.name, safe="")
        data = read_json_url(f"https://pypi.org/pypi/{quoted}/json")
        releases = data.get("releases", {})
        versions = []
        requested = None

        for version, files in releases.items():
            published_values = [
                parse_datetime(file_info.get("upload_time_iso_8601") or file_info.get("upload_time"))
                for file_info in files
                if isinstance(file_info, dict)
            ]
            published_values = [value for value in published_values if value is not None]
            published_at = min(published_values) if published_values else None
            info = VersionInfo(version=version, published_at=published_at)
            if version == dependency.version:
                requested = info
            if published_at and published_at <= cutoff and is_stable_version(version):
                versions.append(info)

        newest_allowed = max(versions, key=lambda item: version_key(item.version), default=None)
        return RegistryResult(requested=requested, newest_allowed=newest_allowed)

    def _resolve_cargo(self, dependency: Dependency, cutoff: datetime) -> RegistryResult:
        quoted = urllib.parse.quote(dependency.name, safe="")
        data = read_json_url(f"https://crates.io/api/v1/crates/{quoted}")
        versions = []
        requested = None

        for item in data.get("versions", []):
            if not isinstance(item, dict) or item.get("yanked"):
                continue
            version = str(item.get("num", ""))
            published_at = parse_datetime(item.get("created_at"))
            info = VersionInfo(version=version, published_at=published_at)
            if version == dependency.version:
                requested = info
            if published_at and published_at <= cutoff and is_stable_version(version):
                versions.append(info)

        newest_allowed = max(versions, key=lambda item: version_key(item.version), default=None)
        return RegistryResult(requested=requested, newest_allowed=newest_allowed)


class VulnerabilityResolver:
    def find_vulnerabilities(self, dependency: Dependency) -> tuple[Vulnerability, ...]:
        ecosystem = OSV_ECOSYSTEMS.get(dependency.manager)
        if not ecosystem:
            return ()

        payload = {
            "package": {
                "ecosystem": ecosystem,
                "name": dependency.name,
            },
            "version": dependency.version,
        }
        data = post_json_url("https://api.osv.dev/v1/query", payload)
        vulnerabilities = []

        for item in data.get("vulns", []):
            if not isinstance(item, dict):
                continue
            vuln_id = str(item.get("id") or "unknown")
            summary = str(item.get("summary") or "").strip()
            vulnerabilities.append(Vulnerability(vuln_id=vuln_id, summary=summary))

        return tuple(vulnerabilities)


def next_file_marker(line: str) -> bool:
    return line.startswith("*** Add File: ") or line.startswith("*** Update File: ") or line.startswith("*** Delete File: ")


def apply_hunk_lines(current: list[str], hunk_lines: list[str], cursor: int) -> tuple[list[str], int]:
    old_seq: list[str] = []
    new_seq: list[str] = []

    for line in hunk_lines:
        if line.startswith("@@"):
            continue
        if line.startswith("\\ No newline"):
            continue
        if not line:
            raise PatchApplyError("Malformed empty patch line")

        marker = line[0]
        value = line[1:]
        if marker == " ":
            old_seq.append(value)
            new_seq.append(value)
        elif marker == "-":
            old_seq.append(value)
        elif marker == "+":
            new_seq.append(value)
        else:
            raise PatchApplyError(f"Unsupported patch marker: {marker}")

    if not old_seq:
        current[cursor:cursor] = new_seq
        return current, cursor + len(new_seq)

    for index in range(cursor, len(current) - len(old_seq) + 1):
        if current[index : index + len(old_seq)] == old_seq:
            current[index : index + len(old_seq)] = new_seq
            return current, index + len(new_seq)

    for index in range(0, len(current) - len(old_seq) + 1):
        if current[index : index + len(old_seq)] == old_seq:
            current[index : index + len(old_seq)] = new_seq
            return current, index + len(new_seq)

    raise PatchApplyError("Could not match patch context")


def apply_update_patch(original: str, body: list[str]) -> str:
    current = original.splitlines()
    cursor = 0
    hunk: list[str] = []

    for line in body:
        if line.startswith("@@"):
            if hunk:
                current, cursor = apply_hunk_lines(current, hunk, cursor)
            hunk = [line]
        else:
            if not hunk:
                continue
            hunk.append(line)

    if hunk:
        current, cursor = apply_hunk_lines(current, hunk, cursor)

    return "\n".join(current) + ("\n" if original.endswith("\n") or current else "")


def reverse_patch_body(body: list[str]) -> list[str]:
    reversed_body: list[str] = []
    for line in body:
        if line.startswith("+"):
            reversed_body.append("-" + line[1:])
        elif line.startswith("-"):
            reversed_body.append("+" + line[1:])
        else:
            reversed_body.append(line)
    return reversed_body


def reconstruct_patched_files(patch: str, cwd: Path, patch_already_applied: bool = False) -> list[PatchedFile]:
    lines = patch.splitlines()
    files: list[PatchedFile] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if line.startswith("*** Add File: "):
            path = line.removeprefix("*** Add File: ").strip()
            index += 1
            added: list[str] = []
            while index < len(lines) and not next_file_marker(lines[index]) and lines[index] != "*** End Patch":
                if lines[index].startswith("+"):
                    added.append(lines[index][1:])
                index += 1
            added_content = "\n".join(added) + ("\n" if added else "")
            if patch_already_applied:
                full_path = (cwd / path).resolve()
                try:
                    added_content = full_path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    pass
            files.append(PatchedFile(path=path, content=added_content, original_content=None))
            continue

        if line.startswith("*** Update File: "):
            path = line.removeprefix("*** Update File: ").strip()
            index += 1
            body: list[str] = []
            while index < len(lines) and not next_file_marker(lines[index]) and lines[index] != "*** End Patch":
                if not lines[index].startswith("*** Move to: "):
                    body.append(lines[index])
                index += 1
            full_path = (cwd / path).resolve()
            try:
                disk_content = full_path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise PatchApplyError(f"Cannot update missing file: {path}") from exc
            if patch_already_applied:
                original = apply_update_patch(disk_content, reverse_patch_body(body))
                content = disk_content
            else:
                original = disk_content
                content = apply_update_patch(original, body)
            files.append(PatchedFile(path=path, content=content, original_content=original))
            continue

        if line.startswith("*** Delete File: "):
            path = line.removeprefix("*** Delete File: ").strip()
            original = None
            if not patch_already_applied:
                full_path = (cwd / path).resolve()
                try:
                    original = full_path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    pass
            files.append(PatchedFile(path=path, content=None, original_content=original))

        index += 1

    return files


def is_dependency_file(path: str) -> bool:
    name = Path(path).name
    return name in {"package.json", "Cargo.toml", "requirements.txt", "pyproject.toml", "go.mod"} or path.endswith(".py")


def extract_npm_dependencies(file_path: str, content: str) -> list[Dependency]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []

    dependencies: list[Dependency] = []
    for dep_type in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        for name, spec in data.get(dep_type, {}).items():
            if not isinstance(spec, str):
                continue
            version = extract_base_version(spec)
            if version:
                dependencies.append(Dependency("npm", file_path, name, spec, version, dep_type))
    return dependencies


def extract_requirements_dependencies(file_path: str, content: str) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if any(marker in stripped for marker in ("http://", "https://", "git+", "file://", "/", "\\")):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([><=~!]+[^;#\s]+)", stripped)
        if not match:
            continue
        name = match.group(1)
        spec = match.group(2)
        version = extract_base_version(spec)
        if version:
            dependencies.append(Dependency("pypi", file_path, name, spec, version, "requirements"))
    return dependencies


def extract_pyproject_dependencies(file_path: str, content: str) -> list[Dependency]:
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(content)
    except Exception:
        return []

    dependencies: list[Dependency] = []
    project = data.get("project", {})
    groups: list[tuple[str, list[str]]] = [("project", project.get("dependencies", []))]
    optional = project.get("optional-dependencies", {})
    groups.extend((f"optional[{name}]", values) for name, values in optional.items())

    for dep_type, values in groups:
        for dep in values:
            if not isinstance(dep, str):
                continue
            if any(marker in dep for marker in ("http://", "https://", "git+", "file://", "@", "/", "\\")):
                continue
            match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([><=~!]+[^;#\s]+)", dep)
            if not match:
                continue
            name = match.group(1)
            spec = match.group(2)
            version = extract_base_version(spec)
            if version:
                dependencies.append(Dependency("pypi", file_path, name, spec, version, dep_type))
    return dependencies


def extract_inline_python_dependencies(file_path: str, content: str) -> list[Dependency]:
    if tomllib is None:
        return []
    metadata_lines: list[str] = []
    in_block = False

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "# /// script":
            in_block = True
            continue
        if stripped == "# ///" and in_block:
            break
        if in_block and line.startswith("#"):
            metadata_lines.append(line[1:].strip())

    if not metadata_lines:
        return []

    try:
        data = tomllib.loads("\n".join(metadata_lines))
    except Exception:
        return []

    dependencies: list[Dependency] = []
    for dep in data.get("dependencies", []):
        if not isinstance(dep, str):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([><=~!]+[^;#\s]+)", dep)
        if not match:
            continue
        name = match.group(1)
        spec = match.group(2)
        version = extract_base_version(spec)
        if version:
            dependencies.append(Dependency("pypi", file_path, name, spec, version, "inline"))
    return dependencies


def extract_cargo_dependencies(file_path: str, content: str) -> list[Dependency]:
    dependencies: list[Dependency] = []
    current_section = ""

    for line in content.splitlines():
        stripped = line.strip()
        section = re.match(r"^\[([^\]]+)\]$", stripped)
        if section:
            current_section = section.group(1)
            continue
        if not current_section.endswith("dependencies") or not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_-]+)\s*=\s*(.+)$", stripped)
        if not match:
            continue
        name = match.group(1)
        raw_spec = match.group(2)
        if "path" in raw_spec or "workspace" in raw_spec:
            continue
        version = extract_base_version(raw_spec)
        if version:
            dependencies.append(Dependency("cargo", file_path, name, raw_spec, version, current_section))
    return dependencies


def extract_dependencies(file_path: str, content: str) -> list[Dependency]:
    raw_dependencies = []
    for parser_name in parser_names_for_file(file_path):
        raw_dependencies.extend(load_parser(parser_name).extract_dependencies(file_path, content))
    return [Dependency(**dependency) for dependency in raw_dependencies]


def dependency_identity(dependency: Dependency) -> tuple[str, str, str, str]:
    return (dependency.manager, dependency.file_path, dependency.name, dependency.dep_type)


def dependency_map(dependencies: list[Dependency]) -> dict[tuple[str, str, str, str], Dependency]:
    return {dependency_identity(dependency): dependency for dependency in dependencies}


def changed_dependencies_for_file(patched_file: PatchedFile) -> list[Dependency]:
    if patched_file.content is None:
        return []

    proposed = extract_dependencies(patched_file.path, patched_file.content)
    if patched_file.original_content is None:
        return proposed

    original_by_key = dependency_map(extract_dependencies(patched_file.path, patched_file.original_content))
    changed = []
    for dependency in proposed:
        original = original_by_key.get(dependency_identity(dependency))
        if original is None or original.spec != dependency.spec or original.version != dependency.version:
            changed.append(dependency)
    return changed


def advisory_dependencies_for_file(patched_file: PatchedFile) -> list[Dependency]:
    if patched_file.content is None:
        return []

    proposed = extract_dependencies(patched_file.path, patched_file.content)
    changed_keys = {dependency_identity(dependency) for dependency in changed_dependencies_for_file(patched_file)}
    return [dependency for dependency in proposed if dependency_identity(dependency) not in changed_keys]


def changed_dependencies_for_patched_files(patched_files: list[PatchedFile]) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for patched_file in patched_files:
        if patched_file.content is None or not is_dependency_file(patched_file.path):
            continue
        dependencies.extend(changed_dependencies_for_file(patched_file))
    return dependencies


def advisory_dependencies_for_patched_files(patched_files: list[PatchedFile]) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for patched_file in patched_files:
        if patched_file.content is None or not is_dependency_file(patched_file.path):
            continue
        dependencies.extend(advisory_dependencies_for_file(patched_file))
    return dependencies


def parser_names_for_file(file_path: str) -> list[str]:
    name = Path(file_path).name
    if name == "package.json":
        return ["parser-npm.py"]
    if name in {"requirements.txt", "pyproject.toml"} or file_path.endswith(".py"):
        return ["parser-python.py"]
    if name == "Cargo.toml":
        return ["parser-cargo.py"]
    if name == "go.mod":
        return ["parser-go.py"]
    return []


def load_parser(file_name: str) -> Any:
    parser_path = Path(__file__).with_name(file_name)
    module_name = file_name.removesuffix(".py").replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load parser: {file_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate_dependencies(
    dependencies: list[Dependency],
    resolver: RegistryResolver,
    vulnerability_resolver: VulnerabilityResolver | None = None,
    now: datetime | None = None,
    min_age_days: int = MIN_PACKAGE_AGE_DAYS,
) -> list[DependencyIssue]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=min_age_days)
    issues: list[DependencyIssue] = []
    vulnerability_resolver = vulnerability_resolver or VulnerabilityResolver()

    for dependency in dependencies:
        vulnerabilities: tuple[Vulnerability, ...] = ()
        try:
            vulnerabilities = vulnerability_resolver.find_vulnerabilities(dependency)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            issues.append(
                DependencyIssue(
                    dependency=dependency,
                    reason=f"Could not verify vulnerability metadata: {exc}",
                    requested=None,
                    newest_allowed=None,
                )
            )
            continue

        try:
            result = resolver.resolve(dependency, cutoff)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            issues.append(
                DependencyIssue(
                    dependency=dependency,
                    reason=f"Could not verify registry metadata: {exc}",
                    requested=None,
                    newest_allowed=None,
                )
            )
            continue

        requested = result.requested
        newest_allowed = result.newest_allowed

        if vulnerabilities:
            sample = ", ".join(vuln.vuln_id for vuln in vulnerabilities[:5])
            extra = f" and {len(vulnerabilities) - 5} more" if len(vulnerabilities) > 5 else ""
            issues.append(
                DependencyIssue(
                    dependency=dependency,
                    reason=f"known vulnerabilities affect requested version: {sample}{extra}",
                    requested=requested,
                    newest_allowed=newest_allowed,
                    vulnerabilities=vulnerabilities,
                )
            )
            continue

        if requested and requested.published_at and requested.published_at > cutoff:
            issues.append(
                DependencyIssue(
                    dependency=dependency,
                    reason=f"requested version is newer than {min_age_days} days",
                    requested=requested,
                    newest_allowed=newest_allowed,
                    vulnerabilities=vulnerabilities,
                )
            )
            continue

        if newest_allowed and is_newer(newest_allowed.version, dependency.version):
            issues.append(
                DependencyIssue(
                    dependency=dependency,
                    reason=f"newer eligible version exists: {newest_allowed.version}",
                    requested=requested,
                    newest_allowed=newest_allowed,
                    vulnerabilities=vulnerabilities,
                )
            )

    return issues


def format_issue(issue: DependencyIssue) -> str:
    dep = issue.dependency
    requested_date = issue.requested.published_at.date().isoformat() if issue.requested and issue.requested.published_at else "unknown"
    newest_allowed = issue.newest_allowed.version if issue.newest_allowed else "none"
    line = (
        f"- {dep.name} ({dep.manager}, {dep.file_path}, {dep.dep_type}): "
        f"{dep.spec} -> {issue.reason}; requested published={requested_date}; "
        f"newest >= {MIN_PACKAGE_AGE_DAYS}d old={newest_allowed}"
    )
    if issue.vulnerabilities:
        summaries = [vuln.summary for vuln in issue.vulnerabilities[:3] if vuln.summary]
        if summaries:
            line += "; advisories: " + " | ".join(summaries)
    return line


def format_report(issues: list[DependencyIssue]) -> str:
    lines = [
        "Dependency policy blocked this edit.",
        f"Packages must use versions published at least {MIN_PACKAGE_AGE_DAYS} days ago.",
        "Packages with known vulnerabilities are blocked.",
        "Issues:",
    ]
    lines.extend(format_issue(issue) for issue in issues)
    return "\n".join(lines)


def format_advisory_report(issues: list[DependencyIssue]) -> str:
    lines = [
        "Dependency advisory for unchanged packages in touched dependency files.",
        "Do not block the completed edit, but tell the user these existing dependencies may need attention:",
    ]
    lines.extend(format_issue(issue) for issue in issues)
    return "\n".join(lines)


def deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def advise(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": reason,
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({}))
        return 0

    hook_event_name = payload.get("hook_event_name")
    if hook_event_name not in {"PreToolUse", "PostToolUse"} or payload.get("tool_name") != "apply_patch":
        print(json.dumps({}))
        return 0

    patch = payload.get("tool_input", {}).get("command", "")
    if not isinstance(patch, str) or not patch.strip():
        print(json.dumps({}))
        return 0

    cwd = Path(payload.get("cwd") or os.getcwd())
    try:
        patched_files = reconstruct_patched_files(
            patch,
            cwd,
            patch_already_applied=hook_event_name == "PostToolUse",
        )
    except PatchApplyError as exc:
        if hook_event_name == "PreToolUse":
            print(json.dumps(deny(f"Could not evaluate dependency patch safely: {exc}")))
        else:
            print(json.dumps({}))
        return 0

    if hook_event_name == "PreToolUse":
        dependencies = changed_dependencies_for_patched_files(patched_files)
        if not dependencies:
            print(json.dumps({}))
            return 0

        issues = evaluate_dependencies(dependencies, RegistryResolver())
        if issues:
            print(json.dumps(deny(format_report(issues))))
        else:
            print(json.dumps({}))
        return 0

    dependencies = advisory_dependencies_for_patched_files(patched_files)
    if not dependencies:
        print(json.dumps({}))
        return 0

    issues = evaluate_dependencies(dependencies, RegistryResolver())
    if issues:
        print(json.dumps(advise(format_advisory_report(issues))))
    else:
        print(json.dumps({}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
