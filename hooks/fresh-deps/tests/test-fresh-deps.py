import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / ".codex" / "hooks" / "fresh-deps.py"
SPEC = importlib.util.spec_from_file_location("dependency_checker", MODULE_PATH)
dependency_checker = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["dependency_checker"] = dependency_checker
SPEC.loader.exec_module(dependency_checker)


class FakeResolver:
    def __init__(self, versions):
        self.versions = versions

    def resolve(self, dependency, cutoff):
        package_versions = self.versions[dependency.name]
        requested = package_versions.get(dependency.version)
        eligible = [
            item
            for item in package_versions.values()
            if item.published_at
            and item.published_at <= cutoff
            and dependency_checker.is_stable_version(item.version)
        ]
        newest_allowed = max(eligible, key=lambda item: dependency_checker.version_key(item.version), default=None)
        return dependency_checker.RegistryResult(requested=requested, newest_allowed=newest_allowed)


class FakeVulnerabilityResolver:
    def __init__(self, vulnerabilities=None):
        self.vulnerabilities = vulnerabilities or {}

    def find_vulnerabilities(self, dependency):
        return tuple(self.vulnerabilities.get(dependency.name, ()))


class DependencyCheckerTests(unittest.TestCase):
    def test_registry_resolver_exposes_supported_ecosystems(self):
        resolver = dependency_checker.RegistryResolver()

        self.assertTrue(hasattr(resolver, "_resolve_npm"))
        self.assertTrue(hasattr(resolver, "_resolve_pypi"))
        self.assertTrue(hasattr(resolver, "_resolve_cargo"))
        self.assertTrue(hasattr(resolver, "_resolve_go"))

    def test_reconstructs_added_package_json(self):
        patch = """*** Begin Patch
*** Add File: package.json
+{"dependencies":{"left-pad":"1.3.0"}}
*** End Patch
"""
        files = dependency_checker.reconstruct_patched_files(patch, Path.cwd())
        self.assertEqual(files[0].path, "package.json")
        self.assertIn("left-pad", files[0].content)

    def test_blocks_requested_version_younger_than_seven_days(self):
        now = datetime(2026, 4, 27, tzinfo=UTC)
        dep = dependency_checker.Dependency("npm", "package.json", "fresh-lib", "2.0.0", "2.0.0", "dependencies")
        resolver = FakeResolver(
            {
                "fresh-lib": {
                    "1.9.0": dependency_checker.VersionInfo("1.9.0", now - timedelta(days=30)),
                    "2.0.0": dependency_checker.VersionInfo("2.0.0", now - timedelta(days=1)),
                }
            }
        )

        issues = dependency_checker.evaluate_dependencies(
            [dep],
            resolver,
            FakeVulnerabilityResolver(),
            now=now,
            min_age_days=7,
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("newer than 7 days", issues[0].reason)

    def test_blocks_when_newer_eligible_version_exists(self):
        now = datetime(2026, 4, 27, tzinfo=UTC)
        dep = dependency_checker.Dependency("npm", "package.json", "old-lib", "^1.0.0", "1.0.0", "dependencies")
        resolver = FakeResolver(
            {
                "old-lib": {
                    "1.0.0": dependency_checker.VersionInfo("1.0.0", now - timedelta(days=100)),
                    "1.2.0": dependency_checker.VersionInfo("1.2.0", now - timedelta(days=10)),
                    "1.3.0": dependency_checker.VersionInfo("1.3.0", now - timedelta(days=1)),
                    "2.0.0-beta.1": dependency_checker.VersionInfo("2.0.0-beta.1", now - timedelta(days=30)),
                }
            }
        )

        issues = dependency_checker.evaluate_dependencies(
            [dep],
            resolver,
            FakeVulnerabilityResolver(),
            now=now,
            min_age_days=7,
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("1.2.0", issues[0].reason)

    def test_allows_newest_eligible_version(self):
        now = datetime(2026, 4, 27, tzinfo=UTC)
        dep = dependency_checker.Dependency("npm", "package.json", "ok-lib", "^1.2.0", "1.2.0", "dependencies")
        resolver = FakeResolver(
            {
                "ok-lib": {
                    "1.2.0": dependency_checker.VersionInfo("1.2.0", now - timedelta(days=10)),
                    "1.3.0": dependency_checker.VersionInfo("1.3.0", now - timedelta(days=1)),
                }
            }
        )

        issues = dependency_checker.evaluate_dependencies(
            [dep],
            resolver,
            FakeVulnerabilityResolver(),
            now=now,
            min_age_days=7,
        )

        self.assertEqual(issues, [])

    def test_pretooluse_payload_denies_apply_patch_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            payload = {
                "session_id": "test",
                "transcript_path": None,
                "cwd": str(cwd),
                "hook_event_name": "PreToolUse",
                "model": "gpt-test",
                "permission_mode": "default",
                "turn_id": "turn-test",
                "tool_name": "apply_patch",
                "tool_use_id": "tool-test",
                "tool_input": {
                    "command": """*** Begin Patch
*** Add File: package.json
+{"dependencies":{"fresh-lib":"2.0.0"}}
*** End Patch
"""
                },
            }

            deps = []
            files = dependency_checker.reconstruct_patched_files(payload["tool_input"]["command"], cwd)
            for patched_file in files:
                deps.extend(dependency_checker.extract_dependencies(patched_file.path, patched_file.content))

            self.assertEqual(deps[0].name, "fresh-lib")
            deny = dependency_checker.deny("blocked")
            self.assertEqual(
                deny["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )

    def test_unchanged_dependencies_do_not_block_pretooluse(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "package.json").write_text(
                '{\n'
                '  "scripts": {\n'
                '    "test": "old"\n'
                '  },\n'
                '  "dependencies": {\n'
                '    "express": "4.18.2"\n'
                '  }\n'
                '}\n',
                encoding="utf-8",
            )
            patch = """*** Begin Patch
*** Update File: package.json
@@
-    "test": "old"
+    "test": "new"
*** End Patch
"""
            files = dependency_checker.reconstruct_patched_files(patch, cwd)

            changed = dependency_checker.changed_dependencies_for_patched_files(files)

            self.assertEqual(changed, [])

    def test_pretooluse_evaluates_only_changed_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "package.json").write_text(
                '{\n'
                '  "dependencies": {\n'
                '    "express": "4.18.2",\n'
                '    "left-pad": "1.1.0"\n'
                '  }\n'
                '}\n',
                encoding="utf-8",
            )
            patch = """*** Begin Patch
*** Update File: package.json
@@
-    "left-pad": "1.1.0"
+    "left-pad": "1.3.0"
*** End Patch
"""
            files = dependency_checker.reconstruct_patched_files(patch, cwd)

            changed = dependency_checker.changed_dependencies_for_patched_files(files)

            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0].name, "left-pad")
            self.assertEqual(changed[0].version, "1.3.0")

    def test_posttooluse_advises_on_unchanged_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "package.json").write_text(
                '{\n'
                '  "scripts": {\n'
                '    "test": "new"\n'
                '  },\n'
                '  "dependencies": {\n'
                '    "express": "4.18.2"\n'
                '  }\n'
                '}\n',
                encoding="utf-8",
            )
            patch = """*** Begin Patch
*** Update File: package.json
@@
-    "test": "old"
+    "test": "new"
*** End Patch
"""
            files = dependency_checker.reconstruct_patched_files(patch, cwd, patch_already_applied=True)

            advisory = dependency_checker.advisory_dependencies_for_patched_files(files)

            self.assertEqual(len(advisory), 1)
            self.assertEqual(advisory[0].name, "express")

    def test_extracts_supported_dependency_formats(self):
        cases = [
            (
                "requirements.txt",
                "requests==2.25.0\n",
                ("pypi", "requests", "2.25.0", "requirements"),
            ),
            (
                "pyproject.toml",
                '[project]\ndependencies = ["requests==2.25.0"]\n',
                ("pypi", "requests", "2.25.0", "project"),
            ),
            (
                "Cargo.toml",
                '[dependencies]\nserde = "1.0.0"\n',
                ("cargo", "serde", "1.0.0", "dependencies"),
            ),
            (
                "script.py",
                '# /// script\n# dependencies = ["requests==2.25.0"]\n# ///\n',
                ("pypi", "requests", "2.25.0", "inline"),
            ),
            (
                "go.mod",
                "module example.com/fake\n\nrequire github.com/gin-gonic/gin v1.10.0\n",
                ("go", "github.com/gin-gonic/gin", "v1.10.0", "require"),
            ),
        ]

        for file_path, content, expected in cases:
            with self.subTest(file_path=file_path):
                deps = dependency_checker.extract_dependencies(file_path, content)
                self.assertEqual(len(deps), 1)
                self.assertEqual(
                    (deps[0].manager, deps[0].name, deps[0].version, deps[0].dep_type),
                    expected,
                )

    def test_extracts_common_pyproject_dependency_sections(self):
        content = """
[project]
dependencies = ["requests==2.25.0"]

[project.optional-dependencies]
docs = ["mkdocs==1.6.1"]

[dependency-groups]
dev = ["pytest==8.3.5"]

[tool.poetry.dependencies]
python = "^3.11"
django = "^1.2.0"
local-lib = { path = "../local-lib" }

[tool.poetry.dev-dependencies]
ruff = "0.6.9"

[tool.poetry.group.test.dependencies]
coverage = { version = "^7.6.1", optional = true }
"""

        deps = dependency_checker.extract_dependencies("pyproject.toml", content)

        self.assertEqual(
            [(dep.name, dep.version, dep.dep_type) for dep in deps],
            [
                ("requests", "2.25.0", "project"),
                ("mkdocs", "1.6.1", "optional[docs]"),
                ("pytest", "8.3.5", "dependency-group[dev]"),
                ("django", "1.2.0", "poetry.dependencies"),
                ("ruff", "0.6.9", "poetry.dev-dependencies"),
                ("coverage", "7.6.1", "poetry.group[test]"),
            ],
        )

    def test_seven_day_policy_applies_to_supported_formats(self):
        now = datetime(2026, 4, 27, tzinfo=UTC)
        cases = [
            (
                "package.json",
                '{"dependencies":{"fresh-npm":"2.0.0"}}\n',
                "fresh-npm",
            ),
            (
                "requirements.txt",
                "fresh-pypi==2.0.0\n",
                "fresh-pypi",
            ),
            (
                "pyproject.toml",
                '[project]\ndependencies = ["fresh-pyproject==2.0.0"]\n',
                "fresh-pyproject",
            ),
            (
                "Cargo.toml",
                '[dependencies]\nfresh_cargo = "2.0.0"\n',
                "fresh_cargo",
            ),
            (
                "script.py",
                '# /// script\n# dependencies = ["fresh-inline==2.0.0"]\n# ///\n',
                "fresh-inline",
            ),
            (
                "go.mod",
                "module example.com/fake\n\nrequire example.com/fresh-go v2.0.0\n",
                "example.com/fresh-go",
            ),
        ]

        versions = {
            package_name: {
                ("v1.0.0" if package_name.endswith("fresh-go") else "1.0.0"): dependency_checker.VersionInfo(
                    "v1.0.0" if package_name.endswith("fresh-go") else "1.0.0",
                    now - timedelta(days=30),
                ),
                ("v2.0.0" if package_name.endswith("fresh-go") else "2.0.0"): dependency_checker.VersionInfo(
                    "v2.0.0" if package_name.endswith("fresh-go") else "2.0.0",
                    now - timedelta(days=1),
                ),
            }
            for _, _, package_name in cases
        }
        resolver = FakeResolver(versions)

        for file_path, content, package_name in cases:
            with self.subTest(file_path=file_path):
                deps = dependency_checker.extract_dependencies(file_path, content)
                self.assertEqual(deps[0].name, package_name)

                issues = dependency_checker.evaluate_dependencies(
                    deps,
                    resolver,
                    FakeVulnerabilityResolver(),
                    now=now,
                    min_age_days=7,
                )

                self.assertEqual(len(issues), 1)
                self.assertIn("newer than 7 days", issues[0].reason)

    def test_blocks_known_vulnerable_requested_version(self):
        now = datetime(2026, 4, 27, tzinfo=UTC)
        dep = dependency_checker.Dependency("pypi", "requirements.txt", "django", "==1.2.0", "1.2.0", "requirements")
        resolver = FakeResolver(
            {
                "django": {
                    "1.2.0": dependency_checker.VersionInfo("1.2.0", now - timedelta(days=5000)),
                }
            }
        )
        vulnerabilities = FakeVulnerabilityResolver(
            {
                "django": (
                    dependency_checker.Vulnerability("GHSA-test", "test advisory"),
                )
            }
        )

        issues = dependency_checker.evaluate_dependencies(
            [dep],
            resolver,
            vulnerabilities,
            now=now,
            min_age_days=7,
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("known vulnerabilities", issues[0].reason)
        self.assertEqual(issues[0].vulnerabilities[0].vuln_id, "GHSA-test")


if __name__ == "__main__":
    unittest.main()
