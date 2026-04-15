from __future__ import annotations

import os
import re
from pathlib import Path

import structlog

logger = structlog.get_logger("codebase_context")

IGNORED_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".gradle",
    "target",
    "bin",
    "obj",
    ".idea",
    ".vscode",
    ".vs",
    "vendor",
    "Gemfile.lock",
    "Pods",
    ".terraform",
}

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
}

CONFIG_EXTENSIONS = {
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
    ".env",
    ".cfg",
    ".conf",
}

DEPS_FILES = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "composer.json",
    "pubspec.yaml",
    "Pipfile",
    "poetry.lock",
    "package-lock.json",
    "yarn.lock",
}


def scan_project(project_path: str, max_depth: int = 4, max_files: int = 200) -> dict:
    root = Path(project_path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Project directory not found: {project_path}")
    if not root.is_dir():
        raise ValueError(f"Path is not a directory: {project_path}")

    logger.info("codebase_scan_start", path=str(root))

    dir_tree = _build_dir_tree(root, max_depth, max_files)
    deps = _extract_dependencies(root)
    api_routes = _extract_api_routes(root, max_files)
    db_models = _extract_db_models(root, max_files)
    config_summary = _extract_configs(root)
    key_files = _find_key_files(root)

    file_stats = _count_files(root)

    result = {
        "project_root": str(root),
        "project_name": root.name,
        "directory_tree": dir_tree,
        "file_stats": file_stats,
        "dependencies": deps,
        "api_routes": api_routes,
        "db_models": db_models,
        "config_files": config_summary,
        "key_files": key_files,
    }

    logger.info(
        "codebase_scan_done", routes=len(api_routes), models=len(db_models), deps_count=deps.get("total_count", 0)
    )
    return result


def build_context_string(context: dict) -> str:
    parts = [f"# Codebase: {context['project_name']}", ""]

    parts.append("## Directory Structure")
    parts.append(f"```\n{context['directory_tree']}\n```")
    parts.append("")

    stats = context.get("file_stats", {})
    parts.append("## File Statistics")
    for k, v in stats.items():
        parts.append(f"- {k}: {v}")
    parts.append("")

    if context.get("key_files"):
        parts.append("## Key Files")
        for f in context["key_files"]:
            parts.append(f"- `{f['path']}` ({f['type']})")
        parts.append("")

    deps = context.get("dependencies", {})
    if deps:
        parts.append("## Dependencies")
        for lang, items in deps.items():
            if lang == "total_count":
                continue
            if isinstance(items, list) and len(items) > 0:
                parts.append(f"### {lang}")
                for item in items[:50]:
                    parts.append(f"- {item}")
                parts.append("")

    routes = context.get("api_routes", [])
    if routes:
        parts.append("## API Routes / Endpoints")
        for r in routes[:80]:
            method = r.get("method", "?")
            path = r.get("path", "")
            source = r.get("source", "")
            parts.append(f"- `{method} {path}` — {source}")
        parts.append("")

    models = context.get("db_models", [])
    if models:
        parts.append("## Database Models / Schemas")
        for m in models[:40]:
            name = m.get("name", "")
            fields = m.get("fields", [])
            source = m.get("source", "")
            field_str = ", ".join(fields[:10]) if fields else "?"
            parts.append(f"- `{name}` ({source}): {field_str}")
        parts.append("")

    configs = context.get("config_files", [])
    if configs:
        parts.append("## Configuration Files")
        for c in configs[:20]:
            parts.append(f"- `{c}`")
        parts.append("")

    return "\n".join(parts)


def _build_dir_tree(root: Path, max_depth: int, max_files: int) -> str:
    lines: list[str] = []
    count = 0

    def _walk(dir_path: Path, prefix: str, depth: int):
        nonlocal count
        if depth > max_depth or count > max_files:
            lines.append(f"{prefix}... (truncated)")
            return

        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            return

        for entry in entries:
            if entry.name.startswith(".") and entry.name not in {".env", ".env.example"}:
                continue
            if entry.name in IGNORED_DIRS:
                continue

            count += 1
            if count > max_files:
                lines.append(f"{prefix}... (truncated)")
                return

            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                _walk(entry, prefix + "  ", depth + 1)
            else:
                lines.append(f"{prefix}{entry.name}")

    _walk(root, "", 0)
    return "\n".join(lines)


def _count_files(root: Path) -> dict:
    counts = {"total_files": 0, "code_files": 0, "config_files": 0, "directories": 0}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        counts["directories"] += len(dirnames)
        for f in filenames:
            counts["total_files"] += 1
            ext = Path(f).suffix.lower()
            if ext in CODE_EXTENSIONS:
                counts["code_files"] += 1
            if ext in CONFIG_EXTENSIONS or f in DEPS_FILES:
                counts["config_files"] += 1
    return counts


def _extract_dependencies(root: Path) -> dict:
    deps: dict = {"total_count": 0}

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            import json

            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            items = []
            for section in ["dependencies", "devDependencies"]:
                for name, ver in data.get(section, {}).items():
                    items.append(f"{name}@{ver}")
            deps["npm"] = items
            deps["total_count"] += len(items)
        except Exception:
            pass

    req_txt = root / "requirements.txt"
    if req_txt.exists():
        try:
            lines = [
                line.strip() for line in req_txt.read_text().splitlines() if line.strip() and not line.startswith("#")
            ]
            deps["pip"] = lines
            deps["total_count"] += len(lines)
        except Exception:
            pass

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            matches = re.findall(r'"([a-zA-Z0-9_\-\.]+(?:[<>=!][^"]*)?)"', content)
            if matches:
                deps["pyproject"] = matches
                deps["total_count"] += len(matches)
        except Exception:
            pass

    go_mod = root / "go.mod"
    if go_mod.exists():
        try:
            lines = [line.strip() for line in go_mod.read_text().splitlines() if line.strip().startswith("require")]
            deps["go"] = lines
            deps["total_count"] += len(lines)
        except Exception:
            pass

    return deps


def _extract_api_routes(root: Path, max_files: int) -> list[dict]:
    routes: list[dict] = []
    file_count = 0

    patterns = [
        (
            re.compile(
                r'(?:app|router|Route)\.(get|post|put|delete|patch|head|options)\s*\(\s*["\']([^"\']+)["\']', re.I
            ),
            "express/fastify",
        ),
        (
            re.compile(r'@(Get|Post|Put|Delete|Patch|Head|Options)Mapping\s*\(\s*["\']?([^"\')\s]+)', re.I),
            "spring/java",
        ),
        (
            re.compile(
                r'route\s*\(\s*["\']?(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)["\']?\s*,\s*["\']([^"\']+)["\']', re.I
            ),
            "generic",
        ),
        (re.compile(r'(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*[\'"]([^\'"]+)[\'"]', re.I), "fastapi/flask"),
    ]

    seen_routes: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext not in {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rb", ".php", ".cs"}:
                continue
            file_count += 1
            if file_count > max_files:
                return routes

            fpath = Path(dirpath) / f
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel_path = str(fpath.relative_to(root))

            all_patterns = list(patterns) + [
                (
                    re.compile(r'(?:@app|@router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', re.I),
                    "fastapi",
                ),
                (re.compile(r'\.(get|post|put|delete|patch|head|options)\s*\(\s*[\'"](/[^\'"]*)[\'"]', re.I), "http"),
            ]

            for pat, source_type in all_patterns:
                for match in pat.finditer(content):
                    method = match.group(1).upper()
                    path = match.group(2)
                    if not path.startswith("/"):
                        continue
                    key = f"{method} {path}"
                    if key not in seen_routes:
                        seen_routes.add(key)
                        routes.append({"method": method, "path": path, "source": rel_path})

            for match in re.finditer(
                r'(?:@app|@router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', content, re.I
            ):
                method = match.group(1).upper()
                path = match.group(2)
                routes.append({"method": method, "path": path, "source": rel_path})

            for match in re.finditer(
                r'\.(get|post|put|delete|patch|head|options)\s*\(\s*[\'"](/[^\'"]*)[\'"]', content, re.I
            ):
                method = match.group(1).upper()
                path = match.group(2)
                if path.startswith("/"):
                    routes.append({"method": method, "path": path, "source": rel_path})

    return routes


def _extract_db_models(root: Path, max_files: int) -> list[dict]:
    models: list[dict] = []
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext not in {".py", ".ts", ".js", ".java", ".go", ".rb", ".php", ".prisma"}:
                continue
            file_count += 1
            if file_count > max_files:
                return models

            fpath = Path(dirpath) / f
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel_path = str(fpath.relative_to(root))

            for match in re.finditer(r"class\s+(\w+)\s*\((?:Base|Model|db\.Model|SQLModel|Document)", content):
                name = match.group(1)
                fields = _extract_class_fields(content[match.start() :])
                models.append({"name": name, "fields": fields, "source": rel_path})

            for match in re.finditer(r"model\s+(\w+)\s*\{", content):
                name = match.group(1)
                models.append({"name": name, "fields": [], "source": rel_path})

            for match in re.finditer(r"type\s+(\w+)\s*(?:=|\{)", content):
                if any(
                    kw in content[max(0, match.start() - 100) : match.start()]
                    for kw in ["model", "schema", "interface"]
                ):
                    name = match.group(1)
                    models.append({"name": name, "fields": [], "source": rel_path})

            for match in re.finditer(r"(?:interface|type)\s+(\w+)\s*(?:extends\s+\w+\s*)?\{", content):
                name = match.group(1)
                fields = _extract_ts_interface_fields(content[match.start() :])
                models.append({"name": name, "fields": fields, "source": rel_path})

    return models


def _extract_class_fields(class_body: str) -> list[str]:
    fields = []
    in_class = False
    for line in class_body[:2000].split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if stripped.startswith("class "):
            in_class = True
            continue
        if in_class and ":" in stripped and "=" not in stripped.split(":")[0]:
            field_name = stripped.split(":")[0].strip()
            if field_name.isidentifier() and not field_name.startswith("_"):
                fields.append(field_name)
        if stripped.startswith("def ") or stripped.startswith("class "):
            if in_class and not stripped.startswith("def __init__"):
                break
    return fields[:15]


def _extract_ts_interface_fields(body: str) -> list[str]:
    fields = []
    in_block = False
    brace_count = 0
    for line in body[:2000].split("\n"):
        stripped = line.strip()
        if "{" in stripped:
            in_block = True
            brace_count += stripped.count("{") - stripped.count("}")
            continue
        if in_block:
            brace_count += stripped.count("{") - stripped.count("}")
            if brace_count <= 0:
                break
            if ":" in stripped and "?" not in stripped.split(":")[0].replace(" ", ""):
                field_name = stripped.split(":")[0].strip().rstrip("?").strip()
                if field_name and not field_name.startswith("//") and not field_name.startswith("/*"):
                    fields.append(field_name)
    return fields[:15]


def _extract_configs(root: Path) -> list[str]:
    configs = []
    for item in root.iterdir():
        if item.is_file() and (
            item.suffix in CONFIG_EXTENSIONS or item.name in DEPS_FILES or item.name.startswith(".env")
        ):
            configs.append(item.name)
    return sorted(configs)


def _find_key_files(root: Path) -> list[dict]:
    key_names = {
        "README.md": "readme",
        "README.rst": "readme",
        "README.txt": "readme",
        "Makefile": "build",
        "Dockerfile": "container",
        "docker-compose.yml": "container",
        "docker-compose.yaml": "container",
        ".env.example": "env_template",
        "Dockerfile.dev": "container",
        "Dockerfile.prod": "container",
    }

    key_patterns = [
        ("src/main.*", "entrypoint"),
        ("src/index.*", "entrypoint"),
        ("src/app.*", "entrypoint"),
        ("main.py", "entrypoint"),
        ("app.py", "entrypoint"),
        ("index.ts", "entrypoint"),
        ("index.js", "entrypoint"),
        ("manage.py", "entrypoint"),
        ("manage.py", "django"),
        ("next.config.*", "config"),
        ("vite.config.*", "config"),
        ("webpack.config.*", "config"),
        ("tsconfig.json", "config"),
        ("tailwind.config.*", "config"),
    ]

    found = []
    for name, ftype in key_names.items():
        if (root / name).exists():
            found.append({"path": name, "type": ftype})

    for pattern, ftype in key_patterns:
        matches = list(root.glob(pattern))
        for m in matches:
            rel = str(m.relative_to(root))
            if rel not in [f["path"] for f in found]:
                found.append({"path": rel, "type": ftype})

    return found
