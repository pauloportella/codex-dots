import json
import re


def extract_base_version(spec):
    cleaned = spec.strip().strip('"').strip("'")
    if cleaned.startswith(("file:", "link:", "git:", "git+", "http://", "https://", "workspace:")):
        return None
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][A-Za-z0-9_.-]+)?", cleaned)
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2) or '0'}.{match.group(3) or '0'}"


def extract_dependencies(file_path, content):
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []

    dependencies = []
    for dep_type in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        for name, spec in data.get(dep_type, {}).items():
            if not isinstance(spec, str):
                continue
            version = extract_base_version(spec)
            if version:
                dependencies.append(
                    {
                        "manager": "npm",
                        "file_path": file_path,
                        "name": name,
                        "spec": spec,
                        "version": version,
                        "dep_type": dep_type,
                    }
                )
    return dependencies
