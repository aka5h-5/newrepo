from pathlib import Path
import re

from tree_sitter import Language, Parser
from tree_sitter_typescript import language_typescript, language_tsx
from tree_sitter_python import language as language_py
from tree_sitter_javascript import language as language_js

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}


def _repo_key_for(path: str) -> str:
    parts = str(path).replace("\\", "/").split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else ""


def _get_parser(file_path: str):
    ext = Path(file_path).suffix.lower()
    parser = Parser()

    if ext == ".tsx":
        parser.language = Language(language_tsx())
    elif ext == ".ts":
        parser.language = Language(language_typescript())
    elif ext == ".py":
        parser.language = Language(language_py())
    elif ext in {".js", ".jsx"}:
        parser.language = Language(language_js())
    else:
        return None

    return parser


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _add(ast: dict, file_path: str, key: str, value: str) -> None:
    value = str(value or "").strip().strip("'\"`")
    if value and value not in ast[file_path][key]:
        ast[file_path][key].append(value)


def analyze_files(contents: dict) -> dict:
    ast = {}

    def visit(node, source, file_path):
        node_type = node.type

        if node_type in {"class_definition", "class_declaration"}:
            name = node.child_by_field_name("name")
            if name:
                _add(ast, file_path, "Classes", _text(name, source))

        elif node_type in {"function_declaration", "function_definition"}:
            name = node.child_by_field_name("name")
            if name:
                _add(ast, file_path, "Functions", _text(name, source))

        elif node_type == "method_declaration":
            name = node.child_by_field_name("name")
            if name:
                _add(ast, file_path, "Methods", _text(name, source))

        elif node_type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name and value and value.type in {"arrow_function", "function_expression"}:
                _add(ast, file_path, "Functions", _text(name, source))

        elif node_type in {"import_statement", "import_from_statement"}:
            import_text = _text(node, source)
            patterns = [
                r"from\s+['\"]([^'\"]+)['\"]",
                r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
                r"^\s*from\s+(\.+[\w\.]*|[\w\.]+)\s+import",
                r"^\s*import\s+([^\s,]+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, import_text)
                if match:
                    _add(ast, file_path, "Imports", match.group(1))

        elif node_type in {"call_expression", "call"}:
            fn = node.child_by_field_name("function")
            if fn:
                call_name = _text(fn, source)
                short_name = call_name.split(".")[-1]
                _add(ast, file_path, "Function Calls", call_name)
                _add(ast, file_path, "Function Calls", short_name)

                if "." in call_name:
                    method = call_name.split(".")[-1].lower()
                    if method in {"get", "post", "put", "patch", "delete"}:
                        args = node.child_by_field_name("arguments")
                        if args and args.named_children:
                            route = _text(args.named_children[0], source)
                            _add(ast, file_path, "Routes", f"{method.upper()} {route}")

        elif node_type == "decorator":
            decorator = _text(node, source)
            if "@app." in decorator or "@router." in decorator:
                _add(ast, file_path, "Routes", decorator)

        elif node_type in {"identifier", "property_identifier", "type_identifier"}:
            identifier = _text(node, source)
            if len(identifier) > 1:
                _add(ast, file_path, "Identifiers", identifier)

        for child in node.children:
            visit(child, source, file_path)

    for file_path, content in (contents or {}).items():
        if Path(file_path).suffix.lower() not in SOURCE_EXTENSIONS:
            continue

        parser = _get_parser(file_path)
        if parser is None:
            continue

        ast[file_path] = {
            "repo": _repo_key_for(file_path),
            "Classes": [],
            "Functions": [],
            "Methods": [],
            "Routes": [],
            "Imports": [],
            "Function Calls": [],
            "Identifiers": [],
            "Existing Tests": [],
        }

        source = str(content or "").encode("utf-8", errors="ignore")
        tree = parser.parse(source)
        visit(tree.root_node, source, file_path)

    return ast
