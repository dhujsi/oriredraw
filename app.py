from __future__ import annotations

import argparse
import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from reconstructor import ReconstructionError, Settings, reconstruct, reconstruct_file


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/reconstruct")
def api_reconstruct():
    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "请选择一张 PNG 或 JPG 图片。"}), 400
    try:
        settings = Settings.from_mapping(request.form)
        result = reconstruct(uploaded.read(), settings=settings)
        return jsonify(
            {
                "cp": result["cp"],
                "stats": result["stats"],
                "anchors": result["anchors"],
                "warnings": result["warnings"],
                "overlay_data_uri": result["overlay_data_uri"],
                "reconstruction_data_uri": result["reconstruction_data_uri"],
            }
        )
    except ReconstructionError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:  # Keep the prototype UI useful while exposing an actionable message.
        app.logger.exception("reconstruction failed")
        return jsonify({"error": f"识别失败：{exc}"}), 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="22.5°/a+b√2 折痕图转 .cp 原型")
    parser.add_argument("--analyze", metavar="IMAGE", help="不启动网页，直接分析一张图片")
    parser.add_argument("--cp", metavar="FILE", default="output.cp", help="命令行模式的 .cp 输出路径")
    parser.add_argument("--overlay", metavar="FILE", default="overlay.png", help="命令行模式的叠加预览路径")
    parser.add_argument("--reconstruction", metavar="FILE", default="reconstruction.png", help="纯重建预览路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5055, type=int)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    if arguments.analyze:
        result = reconstruct_file(
            arguments.analyze,
            arguments.cp,
            arguments.overlay,
            arguments.reconstruction,
        )
        print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
        for warning in result["warnings"]:
            print("警告：" + warning)
        print(f"CP: {Path(arguments.cp).resolve()}")
        print(f"叠加预览: {Path(arguments.overlay).resolve()}")
    else:
        app.run(host=arguments.host, port=arguments.port, debug=False)

