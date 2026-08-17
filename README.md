# Oriedraw

把 PNG/JPG 折痕图重建为 Oriedita 可读取的 `.cp` 文件。截图会自动裁出纸张；手机拍摄的平整纸面可以用四个铆钉做透视校正。支持红蓝峰谷线和纯黑几何图，整个算法可在本机或浏览器内离线运行，不调用 AI、云端识别或图片上传接口。

在线版：<https://dhujsi.github.io/oriredraw/>

## 几何规则

1. 默认“严格 22.5°”版本的内部线方向只能是 `22.5° × k`。
2. 四角和四边中点是固定初始取点；整张图最多再使用一个 `a+b√2` 核心参考点。
3. 此后只能从已有射线的精确交点或已有射线与纸边的交点继续发射。
4. 输出线段端点必须来自上述派生节点，长度不能自由指定。
5. 纸张边界写为 `.cp` 类型 `1`，红色峰线为类型 `2`，蓝色谷线为类型 `3`；不使用辅助线类型 `4`。

严格版仍有强笔画像素无法解释时，程序可以另外生成三类低频精确构造版本：已有夹角的角平分线、与 Oriedita 同式的奇数射线局部可平折补线，以及从已有节点连接到目标线段的 `1/2` 至 `1/8` 精确等分点。它们不是自由角拟合；每条新增线都记录作图规则、端点和像素证据，而且不会改写默认严格版。

“像素线方向误差”只用于判断一小段模糊或锯齿像素能否作为候选。自动模式会按线段长度和线宽收紧，校正后最高为 `3°`；通过后，严格版仍替换为精确 `22.5° × k`，备选版则替换为对应作图公式给出的精确方向。

## 浏览器版（GitHub Pages）

静态页面位于 `web/`。GitHub Actions 发布时会把 `foldability.py`、`reconstructor.py` 和 `web_bridge.py` 一起装入站点，由 Pyodide 在 Web Worker 中运行原有 Python、NumPy 和 OpenCV 算法。

浏览器首次打开需要下载 Python/OpenCV 运行环境；加载完成后，图片只在本机浏览器中处理。复杂图的完整射线传播、cAMV 几何复核和峰谷复核可能需要数分钟甚至更久，请保持页面开启。

拍照图建议让整张纸保持平整并露出四个角。选图后点“拍照图：调整四角”，按左上、右上、右下、左下把四个铆钉拖到外框角。四点会做单应性透视还原；它不能修复卷纸、遮挡或近距离广角镜头造成的弯曲畸变。

纯黑线没有峰谷证据。自动识别为纯黑后，几何照常重建，内部线统一以峰线类型导出；Maekawa 和 big-little-big 峰谷检查会明确标记为跳过，不会用 cAMV 猜出一套颜色后冒充图片识别。

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

- 最适合数字绘制、外框完整、背景干净的 CP 图。
- 可以处理轻微旋转、短断口、一般 JPEG 噪声，以及四角可见的平整纸张透视照片。
- 不支持自由角度线、曲线、卷曲纸面、严重镜头畸变、遮住纸角的照片、文字或圆。
- 低饱和度、严重串色的峰谷线会保留为低置信度结果。
- 密集区域仍可能漏掉短线或产生少量错误连接，应先检查叠加预览再导出。
