# Oriedraw

把正方形 PNG/JPG 折痕图重建为 Oriedita 可读取的 `.cp` 文件。支持红蓝峰谷线识别，整个算法可在本机或浏览器内离线运行，不调用 AI、云端识别或图片上传接口。

在线版：<https://dhujsi.github.io/oriredraw/>

## 几何规则

1. 内部线方向只能是 `22.5° × k`。
2. 四角和四边中点是固定初始取点；整张图最多再使用一个 `a+b√2` 核心参考点。
3. 此后只能从已有射线的精确交点或已有射线与纸边的交点继续发射。
4. 输出线段端点必须来自上述派生节点，长度不能自由指定。
5. 纸张边界写为 `.cp` 类型 `1`，红色峰线为类型 `2`，蓝色谷线为类型 `3`；不使用辅助线类型 `4`。

## 浏览器版（GitHub Pages）

静态页面位于 `web/`。GitHub Actions 发布时会把 `foldability.py`、`reconstructor.py` 和 `web_bridge.py` 一起装入站点，由 Pyodide 在 Web Worker 中运行原有 Python、NumPy 和 OpenCV 算法。

浏览器首次打开需要下载 Python/OpenCV 运行环境；加载完成后，图片只在本机浏览器中处理。复杂图的完整射线传播、cAMV 几何复核和峰谷复核可能需要数分钟甚至更久，请保持页面开启。

本地预览：

```powershell
.\prepare_pages_preview.ps1
python -m http.server 4173 --directory .pages-preview
```

然后打开 <http://127.0.0.1:4173>。

## Flask 本地版

```powershell
python -m pip install -r requirements.txt
.\start.ps1
```

然后打开 <http://127.0.0.1:5055>。

命令行直接识别：

```powershell
python app.py --analyze input.png --cp output.cp --overlay overlay.png --reconstruction reconstruction.png
```

## 评估方式

开发时有正确 `.cp` 答案，应直接比较预测和答案的矢量数据：

```powershell
python audit.py compare --prediction output.cp --reference answer.cp --json evaluation.json --svg difference.svg
```

实践运行时没有正确答案，只能检查图片证据、合法角度、悬空线头和 cAMV：

```powershell
python audit.py reliability --prediction output.cp --image input.png --json reliability.json
```

两类报告不能互相替代。运行可靠性报告也不会声称自己测出了真实召回率或正确率。

## 当前适配范围

- 最适合数字绘制、外框完整、背景干净的正方形 CP 图。
- 可以处理轻微旋转、短断口和一般 JPEG 噪声。
- 不支持自由角度线、曲线、严重透视、文字或圆。
- 低饱和度、严重串色的峰谷线会保留为低置信度结果。
- 密集区域仍可能漏掉短线或产生少量错误连接，应先检查叠加预览再导出。
