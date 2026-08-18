# Oriredraw

[中文](#中文) · [English](#english)

在线版 / Live demo: <https://dhujsi.github.io/oriredraw/>  
GitHub: <https://github.com/dhujsi/oriredraw>  
Issues: <https://github.com/dhujsi/oriredraw/issues>

## 中文

将白底红蓝线或白底黑线 PNG/JPG 折痕图重绘为 Oriedita 可读取的 `.cp` 文件。识别器不接受照片、灰底或黑底图；页面带有独立四角透视校正工具，可预览和下载拉正后的正方形 PNG。整个算法可在本机或浏览器内离线运行，不调用 AI、云端识别或图片上传接口。

网页右上角可以切换中文 / English，并可直接进入 GitHub 项目。首次打开会显示一次简短说明；遇到识别问题、异常结果或有改进建议，欢迎通过 Issues 反馈。

### 使用与输入要求

上传白底红蓝线或白底黑线 PNG/JPG。CP 本体尽量无遮挡并保持正方形，周围其他区域会自动裁剪；目前仅支持 22.5° 系设计。

拍照得到的 CP 如果存在透视变形，先用页面里的“透视校正”把纸张四角拉正成正方形并下载，再交给豆包等 AI 图片工具处理成白底红蓝线或白底黑线，最后上传识别。

一般保持默认设置即可。完成后先看“叠加检查”，确认重绘线条与原图基本一致，再下载 `.cp`。

### 几何规则

1. 默认“严格 22.5°”版本的内部线方向只能是 `22.5° × k`。
2. 四角和四边中点是固定初始取点；整张图最多再使用一个 `a+b√2` 核心参考点。
3. 此后只能从已有射线的精确交点或已有射线与纸边的交点继续发射。
4. 输出线段端点必须来自上述派生节点，长度不能自由指定。
5. 纸张边界写为 `.cp` 类型 `1`，红色峰线为类型 `2`，蓝色谷线为类型 `3`；不使用辅助线类型 `4`。

严格版仍有强笔画像素无法解释时，程序可以另外生成三类低频精确构造版本：已有夹角的角平分线、与 Oriedita 同式的奇数射线局部可平折补线，以及从已有节点连接到目标线段的 `1/2` 至 `1/8` 精确等分点。它们不是自由角拟合；每条新增线都记录作图规则、端点和像素证据，而且不会改写默认严格版。

“像素线方向误差”只用于判断一小段模糊或锯齿像素能否作为候选。自动模式会按线段长度和线宽收紧，校正后最高为 `3°`；通过后，严格版仍替换为精确 `22.5° × k`，备选版则替换为对应作图公式给出的精确方向。

### 浏览器版（GitHub Pages）

静态页面位于 `web/`。GitHub Actions 发布时会把 `foldability.py`、`reconstructor.py` 和 `web_bridge.py` 一起装入站点，由 Pyodide 在 Web Worker 中运行原有 Python、NumPy 和 OpenCV 算法。

浏览器首次打开需要下载 Python/OpenCV 运行环境；加载完成后，图片只在本机浏览器中处理。复杂图的完整射线传播、cAMV 几何复核和峰谷复核可能需要数分钟甚至更久，页面会显示当前算法阶段和进度。

识别结果旁会缩略标出唯一 `a+b√2` 核心点及其归一化坐标；未使用额外核心点时会明确显示。

“高级设置”中的“构造与观测偏移上限”控制检测像素射线与精确构造射线之间允许的法向偏移，默认 `4.8px`。它只用于验证两者属于同一条射线，不会移动 `.cp` 坐标；AI 调整图仍漏线时可小幅提高，出现错误交点或近邻平行线时应降低。

纸张像素较小的截图会自动高质量放大到分析尺寸，`.cp` 坐标仍按归一化纸张计算；放大能让像素门限正常工作，但无法恢复截图中已经丢失的线条细节。

纯黑线没有峰谷证据。自动识别为纯黑后，几何照常重绘，内部线统一以峰线类型导出；Maekawa 和 big-little-big 峰谷检查会明确标记为跳过，不会用 cAMV 猜出一套颜色后冒充图片识别。

本地预览：

```powershell
.\prepare_pages_preview.ps1
python -m http.server 4173 --directory .pages-preview
```

然后打开 <http://127.0.0.1:4173>。

### Flask 本地版

```powershell
python -m pip install -r requirements.txt
.\start.ps1
```

然后打开 <http://127.0.0.1:5055>。

命令行直接识别：

```powershell
python app.py --analyze input.png --cp output.cp --overlay overlay.png --reconstruction reconstruction.png
```

### 评估方式

开发时有正确 `.cp` 答案，应直接比较预测和答案的矢量数据：

```powershell
python audit.py compare --prediction output.cp --reference answer.cp --json evaluation.json --svg difference.svg
```

实践运行时没有正确答案，只能检查图片证据、合法角度、悬空线头和 cAMV：

```powershell
python audit.py reliability --prediction output.cp --image input.png --json reliability.json
```

两类报告不能互相替代。运行可靠性报告也不会声称自己测出了真实召回率或正确率。

### 当前适配范围

- 只支持数字绘制、外框完整、白色背景的红蓝线或黑线 CP 图。
- 可以处理轻微旋转、短断口和一般 JPEG 噪声。
- 识别器不接受照片、灰底、黑底、自由角度线、曲线、文字或圆。
- 低饱和度、严重串色的峰谷线会保留为低置信度结果。
- 密集区域仍可能漏掉短线或产生少量错误连接，应先检查叠加预览再导出。

## English

Oriredraw redraws white-background red/blue or black-line PNG/JPG crease-pattern images into `.cp` files readable by Oriedita. The recognizer does not accept photos, gray backgrounds, or black backgrounds directly. A separate four-corner perspective-correction tool is included for previewing and downloading a rectified square PNG. The full algorithm can run locally or offline in the browser without AI, cloud recognition, or image-upload APIs.

The web app has a 中文 / English switch and a direct GitHub repository link in the upper-right corner. A short welcome notice is shown once on first visit; recognition problems, unexpected output, and improvement suggestions are welcome in GitHub Issues.

### Usage and input requirements

Upload a white-background red/blue or white-background black-line PNG/JPG. Keep the CP itself as unobstructed and square as possible; surrounding areas are cropped automatically. Only 22.5°-system designs are currently supported.

If a photographed CP has perspective distortion, first use the built-in **Perspective correction** tool to rectify the four paper corners into a square and download the result. Then use Doubao or another AI image tool to convert it into white-background red/blue or black line art before recognition.

The default settings are appropriate for most images. After processing, check the **Overlay check** view first; download the `.cp` only after the redrawn lines agree reasonably well with the source image.

### Geometric rules

1. In the default **Strict 22.5°** version, every internal line direction must be `22.5° × k`.
2. The four corners and four edge midpoints are fixed initial points; the entire pattern may additionally use at most one `a+b√2` core reference point.
3. Every later ray must originate from an exact intersection of existing rays or from an intersection between an existing ray and the paper boundary.
4. Output segment endpoints must come from those derived nodes; segment lengths cannot be specified freely.
5. Paper boundaries are written as `.cp` type `1`, red mountain folds as type `2`, and blue valley folds as type `3`; auxiliary-line type `4` is not used.

If strong stroke pixels remain unexplained by the strict result, Oriredraw may additionally generate three low-frequency exact-construction variants: angle bisectors of existing angles, local flat-fold completion using the same odd-ray formulation as Oriedita, and exact `1/2` through `1/8` division points on a target segment connected from existing nodes. These are not free-angle fits. Every added line records its construction rule, endpoints, and pixel evidence, and the default strict result is never overwritten.

**Pixel-line direction tolerance** is used only to decide whether a short blurry or jagged pixel segment can be admitted as a candidate. Auto mode tightens the tolerance according to segment length and line width and is capped at `3°` after corner correction. Once admitted, the strict version still replaces it with an exact `22.5° × k` direction; alternative versions use the exact direction produced by their respective construction formulas.

### Browser version (GitHub Pages)

The static site lives in `web/`. During GitHub Actions deployment, `foldability.py`, `reconstructor.py`, and `web_bridge.py` are packaged with the site. Pyodide runs the existing Python, NumPy, and OpenCV algorithm inside a Web Worker.

On first load, the browser must download the Python/OpenCV runtime. After that, images are processed only in the local browser. Full ray propagation, cAMV geometry checks, and mountain/valley checks can take several minutes or longer for complex patterns; the page reports the current algorithm stage and progress.

The result panel also shows the unique `a+b√2` core point and its normalized coordinates when one is used.

In **Advanced settings**, **Construction / observation offset limit** controls the allowed normal offset between an observed pixel ray and its exact construction ray. The default is `4.8px`. It is used only to verify that both observations refer to the same ray and never moves `.cp` coordinates. Raise it slightly when AI-cleaned images still miss lines; lower it when incorrect intersections or nearby parallel lines are being associated.

Small screenshots are automatically upscaled at high quality to the analysis size while `.cp` coordinates remain normalized to the paper. Upscaling keeps pixel thresholds usable but cannot restore line detail already lost in the source image.

Black-line CPs contain no mountain/valley evidence. Geometry is still redrawn normally and internal lines are exported as mountain folds. Maekawa and big-little-big color checks are explicitly skipped; Oriredraw does not use cAMV to invent a color assignment and present it as image recognition.

Local Pages preview:

```powershell
.\prepare_pages_preview.ps1
python -m http.server 4173 --directory .pages-preview
```

Then open <http://127.0.0.1:4173>.

### Local Flask version

```powershell
python -m pip install -r requirements.txt
.\start.ps1
```

Then open <http://127.0.0.1:5055>.

Direct command-line recognition:

```powershell
python app.py --analyze input.png --cp output.cp --overlay overlay.png --reconstruction reconstruction.png
```

### Evaluation

When a ground-truth `.cp` is available during development, compare the prediction and reference as vector data:

```powershell
python audit.py compare --prediction output.cp --reference answer.cp --json evaluation.json --svg difference.svg
```

In real use, no ground-truth answer exists, so only image evidence, legal angles, dangling lineheads, and cAMV consistency can be checked:

```powershell
python audit.py reliability --prediction output.cp --image input.png --json reliability.json
```

The two reports are not interchangeable. A reliability report does not claim to measure true recall or accuracy.

### Current scope

- Supports digitally drawn CPs with a complete border and a white background, using red/blue or black lines.
- Handles slight rotation, short gaps, and ordinary JPEG noise.
- Does not accept photos, gray or black backgrounds, free-angle lines, curves, text, or circles directly.
- Low-saturation or heavily color-contaminated mountain/valley lines may remain low-confidence results.
- Dense regions may still miss short lines or produce a small number of incorrect connections; inspect the overlay before export.
