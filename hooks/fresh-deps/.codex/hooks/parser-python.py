import re

try:
    import tomllib
except ImportError:
    tomllib = None


def extract_base_version(spec):
    cleaned = spec.strip().strip('"').strip("'")
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][A-Za-z0-9_.-]+)?", cleaned)
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2) or '0'}.{match.group(3) or '0'}"


def dependency_record(file_path, name, spec, version, dep_type):
    return {
        "manager": "pypi",
        "file_path": file_path,
        "name": name,
        "spec": spec,
        "version": version,
        "dep_type": dep_type,
    }


def parse_requirement(file_path, dep, dep_type):
    if any(marker in dep for marker in ("http://", "https://", "git+", "file://", "@", "/", "\\")):
        return None
    match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([><=~!]+[^;#\s]+)", dep.strip())
    if not match:
        return None
    version = extract_base_version(match.group(2))
    if not version:
        return None
    return dependency_record(file_path, match.group(1), match.group(2), version, dep_type)


def parse_named_spec(file_path, name, spec, dep_type):
    if name == "python":
        return None
    if isinstance(spec, dict):
        if any(key in spec for key in ("path", "git", "url", "file", "develop")):
            return None
        spec = spec.get("version")
    if not isinstance(spec, str):
        return None
    if any(marker in spec for marker in ("http://", "https://", "git+", "file://", "@", "/", "\\")):
        return None
    version = extract_base_version(spec)
    if not version:
        return None
    return dependency_record(file_path, name, spec, version, dep_type)


def extract_requirement_list(file_path, values, dep_type):
    dependencies = []
    if not isinstance(values, list):
        return dependencies
    for dep in values:
        if not isinstance(dep, str):
            continue
        record = parse_requirement(file_path, dep, dep_type)
        if record:
            dependencies.append(record)
    return dependencies


def extract_named_specs(file_path, values, dep_type):
    dependencies = []
    if not isinstance(values, dict):
        return dependencies
    for name, spec in values.items():
        if not isinstance(name, str):
            continue
        record = parse_named_spec(file_path, name, spec, dep_type)
        if record:
            dependencies.append(record)
    return dependencies


def extract_requirements(file_path, content):
    dependencies = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        dep = parse_requirement(file_path, stripped, "requirements")
        if dep:
            dependencies.append(dep)
    return dependencies


def extract_pyproject(file_path, content):
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(content)
    except Exception:
        return []

    dependencies = []
    project = data.get("project", {})
    dependencies.extend(extract_requirement_list(file_path, project.get("dependencies", []), "project"))
    for name, values in project.get("optional-dependencies", {}).items():
        dependencies.extend(extract_requirement_list(file_path, values, f"optional[{name}]"))

    for name, values in data.get("dependency-groups", {}).items():
        dependencies.extend(extract_requirement_list(file_path, values, f"dependency-group[{name}]"))

    poetry = data.get("tool", {}).get("poetry", {})
    dependencies.extend(extract_named_specs(file_path, poetry.get("dependencies", {}), "poetry.dependencies"))
    dependencies.extend(extract_named_specs(file_path, poetry.get("dev-dependencies", {}), "poetry.dev-dependencies"))
    for name, group in poetry.get("group", {}).items():
        dependencies.extend(
            extract_named_specs(
                file_path,
                group.get("dependencies", {}) if isinstance(group, dict) else {},
                f"poetry.group[{name}]",
            )
        )
    return dependencies


def extract_inline(file_path, content):
    if tomllib is None:
        return []
    metadata_lines = []
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
    dependencies = []
    for dep in data.get("dependencies", []):
        if not isinstance(dep, str):
            continue
        record = parse_requirement(file_path, dep, "inline")
        if record:
            dependencies.append(record)
    return dependencies


def extract_dependencies(file_path, content):
    if file_path.endswith("requirements.txt"):
        return extract_requirements(file_path, content)
    if file_path.endswith("pyproject.toml"):
        return extract_pyproject(file_path, content)
    if file_path.endswith(".py"):
        return extract_inline(file_path, content)
    return []
