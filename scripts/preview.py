from __future__ import annotations

import argparse
import os
import re
import shutil
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / ".preview-site"
PYTHON_FILES = [
    "foldability.py",
    "reconstructor.py",
    "web_bridge.py",
    "construction_search.py",
    "shadow_search.py",
    "shadow_evidence.py",
    "shadow_geometry.py",
    "shadow_geometry_v2.py",
    "shadow_variant.py",
    "provenance_v3.py",
    "provenance_v4.py",
    "selected_geometry_v4.py",
    "shadow_variant_v3.py",
    "isolated_ratio.py",
    "shadow_variant_v4.py",
    "shadow_bridge.py",
]


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()


def assemble() -> str:
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "web", SITE)
    python_dir = SITE / "python"
    python_dir.mkdir(parents=True, exist_ok=True)
    for name in PYTHON_FILES:
        shutil.copy2(ROOT / name, python_dir / name)
    shutil.copy2(ROOT / "VERSION", SITE / "VERSION")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pattern = re.compile(r"^const WEB_ENGINE_VERSION = '.*';$", re.MULTILINE)
    for name in ("app.js", "pyodide-worker.js"):
        path = SITE / name
        text = path.read_text(encoding="utf-8")
        text, count = pattern.subn(
            f"const WEB_ENGINE_VERSION = '{version}';",
            text,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"Could not patch WEB_ENGINE_VERSION in {name}")
        path.write_text(text, encoding="utf-8")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble and serve the current Oriredraw branch locally.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    args = parser.parse_args()

    version = assemble()
    os.chdir(SITE)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), NoCacheHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Oriredraw {version} preview: {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
