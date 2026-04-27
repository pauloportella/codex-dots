import re


def strip_comment(line):
    return line.split("//", 1)[0].strip()


def parse_require_line(file_path, line, dep_type):
    stripped = strip_comment(line)
    if not stripped:
        return None
    if stripped.startswith("require "):
        stripped = stripped.removeprefix("require ").strip()
    parts = stripped.split()
    if len(parts) < 2:
        return None
    name, version = parts[0], parts[1]
    if not version.startswith("v"):
        return None
    return {
        "manager": "go",
        "file_path": file_path,
        "name": name,
        "spec": version,
        "version": version,
        "dep_type": dep_type,
    }


def extract_dependencies(file_path, content):
    dependencies = []
    in_require_block = False

    for line in content.splitlines():
        stripped = strip_comment(line)
        if stripped == "require (":
            in_require_block = True
            continue
        if in_require_block and stripped == ")":
            in_require_block = False
            continue
        if in_require_block:
            record = parse_require_line(file_path, stripped, "require")
            if record:
                dependencies.append(record)
            continue
        if stripped.startswith("require "):
            record = parse_require_line(file_path, stripped, "require")
            if record:
                dependencies.append(record)

    return dependencies
