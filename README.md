# Oriedraw

把白底 CP 图片重建成 Oriedita 可读取的 `.cp` 文件。

在线版：<https://dhujsi.github.io/oriredraw/>

支持白底红蓝线和白底黑线 PNG/JPG。浏览器版会在本机处理图片，不上传服务器，也不调用 AI 或云端识别。

## 使用

1. 选择、拖入或直接粘贴 CP 图片。
2. 一般保持默认参数，点击“开始重建”。
3. 先看“叠加检查”，确认线条位置基本正确后再下载 `.cp`。

照片、灰底和黑底图不适合直接识别。页面带有独立透视校正工具，可以先把倾斜的纸张拉正成正方形；照片本身仍建议先处理成白底红蓝线或黑线线稿。

黑线图没有峰谷信息，因此只能重建几何，内部线会统一按峰线导出。

## 能识别什么

目前主要面向数字绘制、外框完整、白色背景的 CP。轻微旋转、短断口和普通 JPEG 噪声通常可以处理。

自由角度线、曲线、文字、圆，以及严重串色、低清晰度或非常密集的区域都可能影响结果。密集区域尤其可能漏短线或出现少量误连，所以最终以叠加预览为准。

## 构造规则

默认版本按严格的 22.5° 系重建：内部线方向只能是 `22.5° × k`。初始参考来自四角和四边中点，整张图最多再使用一个 `a+b√2` 核心点。后续线段必须从已有射线的交点或纸边交点继续构造，不能直接拟合任意角度和长度。

当严格版仍有明显像素无法解释时，可以额外生成备选构造。备选也必须是精确构造，目前包括角平分、局部可平折补线和 `1/2` 到 `1/8` 等分点，不会改写严格版结果。

`.cp` 中纸张边界使用类型 `1`，红色峰线使用类型 `2`，蓝色谷线使用类型 `3`。

## 高级参数

“方向容差”只负责容忍图片里的锯齿和模糊，不会把近似角度写进 `.cp`。自动模式最高为 `3°`。

“线段识别强度”调高会减少误线，调低更容易保留短线和淡线。

“构造线匹配偏移”允许检测到的像素线与精确构造线存在少量位置偏差，默认 `4.8px`。漏线时可以略微调高；出现误连时应调低。

## 浏览器版

静态页面在 `web/`。GitHub Pages 通过 Pyodide 在 Web Worker 中运行原有 Python、NumPy 和 OpenCV 识别代码。

第一次打开页面需要加载运行环境。加载完成后，图片处理都在当前浏览器里进行。复杂 CP 的重建和 cAMV 复核可能比较慢，页面会显示进度。

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

## 评估

有正确 `.cp` 答案时，可以直接比较预测结果和答案：

```powershell
python audit.py compare --prediction output.cp --reference answer.cp --json evaluation.json --svg difference.svg
```

没有正确答案时，可以检查图片证据、合法角度、悬空线头和 cAMV：

```powershell
python audit.py reliability --prediction output.cp --image input.png --json reliability.json
```

可靠性报告只能检查结果是否可疑，不能当作真实正确率或召回率。
