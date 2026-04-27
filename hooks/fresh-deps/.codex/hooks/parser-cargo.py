import re


def extract_base_version(spec):
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][A-Za-z0-9_.-]+)?", spec)
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2) or '0'}.{match.group(3) or '0'}"


def extract_dependencies(file_path, content):
    dependencies = []
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
            dependencies.append(
                {
                    "manager": "cargo",
                    "file_path": file_path,
                    "name": name,
                    "spec": raw_spec,
                    "version": version,
                    "dep_type": current_section,
                }
            )
    return dependencies
