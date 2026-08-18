const STORAGE_LANGUAGE = 'oriredraw-language';
const STORAGE_WELCOME = 'oriredraw-welcome-v1';
const preferredLanguage = localStorage.getItem(STORAGE_LANGUAGE);
const language = preferredLanguage || (navigator.language?.toLowerCase().startsWith('zh') ? 'zh' : 'en');

const messages = {
  zh: {
    pageTitle: 'Oriredraw · CP 重绘', pageDescription: '在浏览器中把 22.5° 系 CP 图片重绘为 Oriedita .cp 文件。',
    heroTitle: '将图片 CP 重绘为<br><span>.cp 文件</span>', heroIntro: '选择至多一个 <em>a+b√2</em> 核心点，从核心点、四角顶点和四边中点射线，仅由射线交点或纸边交点继续推导直至完成。', privacy: '图片仅在你的浏览器中处理，不会上传服务器。',
    start: '开始重绘', upload: '选择、拖入或粘贴 CP 图片', uploadMeta: 'PNG / JPG · 最大 12 MB', noFile: '尚未选择文件', requirementsTitle: '上传要求', requirements1: '仅支持白底红蓝线或白底黑线。CP 本体尽量无遮挡并保持正方形，程序会自动裁剪周围其他区域。暂时仅支持 22.5° 系设计。', requirements2: '如果是拍照产生透视变形的 CP，可先用下方“透视校正”拉成正方形，再交给豆包等 AI 处理成白底线稿。',
    perspective: '透视校正', perspectiveSummary: '拍照变形时，先把纸张四角拉正成正方形', perspectiveMode: '只负责四角还原，不会开始 CP 识别', rectifyPick: '选择或粘贴需要拉正的变形图', adjustCorners: '调整四角锚点', cornerTitle: '校准图片四角', cornerInstruction: '先在原图粗调四角，再裁剪放大继续精调', cornerDone: '完成并生成正方形', cornerHelp: '依次对准左上、右上、右下、左下；输出保持原图可用分辨率。', cornerCrop: '按四角裁剪并放大', cornerFull: '查看原图', cornerReset: '重置四角', cancel: '取消',
    advanced: '高级设置', mvMode: '线条颜色', mvAuto: '自动判断红蓝 / 纯黑', mvColor: '强制按红蓝图识别', mvMono: '纯黑线：统一按峰线导出', mvHint: '纯黑图没有峰谷信息；程序只重绘几何，不用 cAMV 猜颜色。', angle: '像素线方向误差', autoRecommended: '自动（推荐）', manual: '手动上限', angleHint: '只判断模糊像素是否可能来自合法线；不会把近似角度写进 .cp。四角校正后自动上限为 3°。', support: '线段证据', supportHint: '提高可减少误线，降低可找回较短或较淡的线。', sqrtSnap: '√2 吸附范围', offset: '构造与观测偏移上限', offsetHint: '只关联精确射线与它自己检测到的像素位置，不移动 .cp 坐标。AI 调整图仍漏线时可小幅提高；过高可能绑定错误交点。', variants: '生成精确备选构造版本', variantsHint: '仅在严格 22.5° 版仍有强像素未解释时，另列角平分、局部可平折补线和线段等分版本。',
    waiting: '等待一张折痕图', engineLoading: '正在准备浏览器识别引擎…', emptyCopy: '重绘结果会在这里与原图叠加显示', loadingInitial: '正在寻找合法射线、交点并反复执行 cAMV 复核…', loadingSlow: '复杂图可能需要数分钟甚至更久，请保持页面开启', resultTitle: '重绘结果', download: '下载 .cp', overlay: '叠加检查', clean: '纯重绘', coreNone: '未使用额外核心点', coreHint: '坐标采用纸张中心为原点的归一化 a+b√2 表示。', anchors: '查看种子与迭代构造来源', constructions: '查看本版本新增的精确构造', expand: '展开', collapse: '收起',
    welcomeTitle: '欢迎使用 Oriredraw', welcomeBody: '项目仍在持续迭代。遇到识别问题、异常结果，或者有改进建议，欢迎在 GitHub Issues 提交反馈。图片只在当前浏览器中处理。', welcomeIssues: '前往 GitHub Issues', welcomeClose: '知道了', githubAria: '打开 Oriredraw GitHub 项目', languageAria: 'Switch to English',
  },
  en: {
    pageTitle: 'Oriredraw · CP Redraw', pageDescription: 'Redraw 22.5°-system CP images as Oriedita .cp files directly in your browser.',
    heroTitle: 'Redraw CP images as<br><span>.cp files</span>', heroIntro: 'Choose at most one <em>a+b√2</em> core point; emit rays from the core point, the four corners, and the four edge midpoints, then continue only from ray intersections or ray–paper-edge intersections until complete.', privacy: 'Images are processed only in your browser and are never uploaded.',
    start: 'Start redraw', upload: 'Choose, drop, or paste a CP image', uploadMeta: 'PNG / JPG · up to 12 MB', noFile: 'No file selected', requirementsTitle: 'Input requirements', requirements1: 'White-background red/blue lines or white-background black lines only. Keep the CP itself as unobstructed and square as possible; surrounding areas are cropped automatically. Currently only 22.5°-system designs are supported.', requirements2: 'For a photographed CP with perspective distortion, first use “Perspective correction” below to rectify the paper to a square, then use Doubao or another AI image tool to convert it to white-background line art.',
    perspective: 'Perspective correction', perspectiveSummary: 'Rectify a photographed sheet to a square before recognition', perspectiveMode: 'Corrects the four corners only; it does not start CP recognition', rectifyPick: 'Choose or paste a distorted image', adjustCorners: 'Adjust corner anchors', cornerTitle: 'Calibrate the four corners', cornerInstruction: 'Roughly place the corners first, then crop and zoom for fine adjustment', cornerDone: 'Finish and create square image', cornerHelp: 'Align top-left, top-right, bottom-right, then bottom-left. Output keeps the usable source resolution.', cornerCrop: 'Crop to corners and zoom', cornerFull: 'View original', cornerReset: 'Reset corners', cancel: 'Cancel',
    advanced: 'Advanced settings', mvMode: 'Line colors', mvAuto: 'Auto-detect red/blue or black', mvColor: 'Force red/blue recognition', mvMono: 'Black lines: export all as mountain', mvHint: 'Black-line CPs contain no mountain/valley evidence; only geometry is redrawn, and cAMV is not used to invent colors.', angle: 'Pixel-line direction tolerance', autoRecommended: 'Auto (recommended)', manual: 'Manual limit', angleHint: 'Used only to admit blurry pixels that may belong to a legal line; approximate angles are never written to .cp. Auto mode is capped at 3° after corner correction.', support: 'Segment evidence', supportHint: 'Raise this to reduce false lines; lower it to recover shorter or fainter lines.', sqrtSnap: '√2 snap range', offset: 'Construction / observation offset limit', offsetHint: 'Associates an exact ray with its observed pixel position without moving .cp coordinates. Raise slightly when AI-cleaned images still miss lines; too high may bind the wrong intersection.', variants: 'Generate exact alternative constructions', variantsHint: 'When the strict 22.5° result leaves strong pixels unexplained, also try angle bisectors, local flat-fold completion, and exact segment divisions.',
    waiting: 'Waiting for a crease pattern', engineLoading: 'Preparing the browser recognition engine…', emptyCopy: 'The redrawn result will be overlaid with the source image here', loadingInitial: 'Finding legal rays and intersections and repeatedly running cAMV checks…', loadingSlow: 'Complex patterns may take several minutes or longer; keep this page open', resultTitle: 'Redraw result', download: 'Download .cp', overlay: 'Overlay check', clean: 'Redraw only', coreNone: 'No extra core point used', coreHint: 'Coordinates use the paper center as the origin and normalized a+b√2 notation.', anchors: 'View seed and iterative construction sources', constructions: 'View exact constructions added in this version', expand: 'Open', collapse: 'Close',
    welcomeTitle: 'Welcome to Oriredraw', welcomeBody: 'Oriredraw is still evolving. If you run into recognition issues, unexpected output, or have suggestions, please open an issue on GitHub. Images are processed only in your browser.', welcomeIssues: 'Open GitHub Issues', welcomeClose: 'Got it', githubAria: 'Open the Oriredraw GitHub repository', languageAria: '切换到中文',
  },
};

const message = key => messages[language][key] ?? messages.zh[key] ?? key;

function applyStaticTranslations() {
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  document.title = message('pageTitle');
  const description = document.querySelector('meta[name="description"]');
  if (description) description.content = message('pageDescription');
  document.querySelectorAll('[data-i18n]').forEach(element => { element.textContent = message(element.dataset.i18n); });
  document.querySelectorAll('[data-i18n-html]').forEach(element => { element.innerHTML = message(element.dataset.i18nHtml); });
  const languageToggle = document.querySelector('#language-toggle');
  if (languageToggle) {
    languageToggle.textContent = language === 'zh' ? 'EN' : '中文';
    languageToggle.setAttribute('aria-label', message('languageAria'));
  }
  document.querySelector('#repository-link')?.setAttribute('aria-label', message('githubAria'));
  const paperSummary = document.querySelector('#paper-tool > summary');
  if (paperSummary) {
    paperSummary.dataset.closedLabel = message('expand');
    paperSummary.dataset.openLabel = message('collapse');
  }
}

const exactEnglish = new Map(Object.entries({
  '正在准备浏览器识别引擎…': 'Preparing the browser recognition engine…', '正在加载 Python 运行环境…': 'Loading the Python runtime…', '正在加载 NumPy 与 OpenCV…': 'Loading NumPy and OpenCV…', '正在装入 Oriredraw 重建算法…': 'Loading the Oriredraw redraw engine…', '浏览器识别引擎已就绪': 'Browser recognition engine ready', '正在重建…': 'Redrawing…',
  '读取图片': 'Reading image', '检查白底线稿格式': 'Checking white-background line-art format', '提取黑线与红蓝线像素': 'Extracting black/red/blue line pixels', '寻找候选直线': 'Finding candidate lines', '激活角点、边中点与合法射线': 'Activating corners, edge midpoints, and legal rays', '建立候选交点图': 'Building the candidate-intersection graph', '融合射线与交点证据': 'Fusing ray and intersection evidence', '推导精确交点并清理线头': 'Deriving exact intersections and cleaning lineheads', '闭合有证据支持的折痕路径': 'Closing crease paths supported by image evidence', '执行 cAMV 几何复核': 'Running cAMV geometry checks', '根据 cAMV 反复复核缺失路径': 'Rechecking missing paths with cAMV', '判断红蓝峰谷并完成 cAMV 检验': 'Assigning mountain/valley colors and completing cAMV checks', '生成 CP 与结果预览': 'Generating CP and previews', '生成精确备选构造版本': 'Generating exact alternative constructions', '重建完成': 'Redraw complete', '正在准备白底 CP 线稿…': 'Preparing the white-background CP line art…',
  '识别失败': 'Recognition failed', '识别引擎加载失败，请刷新后重试': 'Recognition engine failed to load. Refresh and try again.', '请选择 PNG 或 JPG 图片。': 'Choose a PNG or JPG image.', '请选择 PNG 或 JPG 图片': 'Choose a PNG or JPG image', '图片超过 12 MB，请压缩后重试。': 'The image is over 12 MB. Compress it and try again.', '请检查图片或调整参数后重试': 'Check the image or adjust the settings, then try again', '尚未选择文件': 'No file selected',
  '正在读取图片…': 'Reading image…', '无法读取图片': 'Unable to read the image', '透视校正失败': 'Perspective correction failed', '正在生成正方形 PNG…': 'Generating square PNG…', '正方形图片已生成，可继续调整或下载': 'Square image created; you can adjust it again or download it', '按四个锚点做透视还原': 'Rectifying perspective from the four anchors', '重新调整四角': 'Adjust corners again', '调整四角锚点': 'Adjust corner anchors', '先在原图粗调四角，再裁剪放大继续精调': 'Roughly place the corners first, then crop and zoom for fine adjustment', '已按当前四角裁剪放大；可继续拖动准星精调': 'Cropped and zoomed to the current corners; continue dragging the crosshairs to fine-tune',
  '自动': 'Auto', '无需校正': 'No correction', '未放大': 'Not upscaled', '未使用额外核心点': 'No extra core point used', '严格 22.5°': 'Strict 22.5°', '可平折补线版': 'Flat-fold completion', '角平分构造版': 'Angle-bisector construction', '线段等分构造版': 'Segment-division construction', '已有夹角的精确角平分线': 'Exact bisector of an existing angle', '三线/奇数射线推可平折补线': 'Flat-fold completion from three/odd rays',
  '由母射线相交': 'From parent-ray intersection', '由已有射线与纸边相交': 'From existing ray / paper-edge intersection', '角点': 'Corner', '内部': 'Interior', '左': 'Left', '右': 'Right', '上': 'Top', '下': 'Bottom', '角点种子': 'Corner seed', '边中点种子': 'Edge-midpoint seed', '唯一内部 a+b√2 种子': 'Unique interior a+b√2 seed', '唯一纸边 a+b√2 种子': 'Unique boundary a+b√2 seed', 'a+b√2 种子': 'a+b√2 seed',
  '分析图尺寸': 'Analysis size', '纸框比例校正': 'Paper aspect correction', '小图自动放大': 'Small-image upscale', '可构造射线': 'Constructible rays', '初始种子射线': 'Seed rays', '唯一代数核心点': 'Algebraic core point', '纸边交点派生射线': 'Paper-edge-derived rays', '全部派生射线': 'Derived rays', '内部线段': 'Internal segments', 'cAMV 结构分': 'cAMV structure score', 'cAMV 可疑节点': 'cAMV suspect nodes', 'cAMV 补回射线': 'cAMV recovered rays', 'cAMV 几何复核轮次': 'cAMV geometry-check rounds', '峰线 / 红': 'Mountain / red', '谷线 / 蓝': 'Valley / blue', '红蓝模糊线': 'Ambiguous M/V', 'cAMV 改色线': 'cAMV recolored lines', '完整 cAMV 异常': 'Full cAMV anomalies', '局部偏移保留线': 'Offset-proxy preserved', '最大验证偏移 px': 'Max validation offset px', '忽略自由角度证据': 'Rejected free-angle evidence',
  '检测为纯黑线 CP：几何照常重建，内部线统一按峰线导出；由于图片没有红蓝证据，本版本不运行 Maekawa 与 big-little-big 改色，也不会把 cAMV 推测颜色冒充识别结果。': 'Black-line CP detected: geometry is redrawn normally and all internal lines are exported as mountain folds. With no red/blue evidence, Maekawa and big-little-big recoloring are skipped, and cAMV-inferred colors are not presented as recognized image data.',
  '当前结果切分较细；这是原型阶段的已知问题，可继续合并同射线上的冗余小段。': 'The current result is split into many small segments. This is a known prototype limitation; redundant collinear pieces can still be merged.', '无法读取图片，请上传 PNG 或 JPG。': 'Unable to read the image. Upload a PNG or JPG.', '图片尺寸过小，最短边至少需要 80 像素。': 'The image is too small; the shortest side must be at least 80 px.', '仅支持白底红蓝线或白底黑线 CP。照片、灰底、黑底及实物图请先交给豆包等 AI 图片工具处理成白底线稿后再上传。': 'Only white-background red/blue or black-line CPs are supported. Convert photos, gray/black backgrounds, or physical objects to white-background line art with Doubao or another AI image tool before uploading.', '图片中没有检测到线条。': 'No lines were detected in the image.', '没有找到足够大的纸张区域。': 'No sufficiently large paper region was found.', '检测到的纸张区域不像正方形；初版要求完整、近似正方形的外框。': 'The detected paper region is not close enough to a square; the current version requires a complete, approximately square border.', '没有检测到直线。': 'No straight lines were detected.', '没有找到足够多的合法交点。': 'Not enough legal intersections were found.', '没有找到足够多的 22.5° 折痕节点。': 'Not enough 22.5° crease nodes were found.', '没有候选线通过 22.5° 与 a+b√2 约束。': 'No candidate lines passed the 22.5° and a+b√2 constraints.', '生成预览图失败。': 'Failed to generate the preview image.',
}));

function translateEnglish(text) {
  if (exactEnglish.has(text)) return exactEnglish.get(text);
  let match = text.match(/^第 (\d+) 代交点$/); if (match) return `Generation ${match[1]} intersection`;
  match = text.match(/^第 (\d+) 代纸边交点$/); if (match) return `Generation ${match[1]} paper-edge intersection`;
  match = text.match(/^备选 (\d+)$/); if (match) return `Alternative ${match[1]}`;
  match = text.match(/^(\d+) 条$/); if (match) return match[1];
  match = text.match(/^误差 ([\d.]+)px$/); if (match) return `error ${match[1]}px`;
  match = text.match(/^证据 (\d+)%$/); if (match) return `evidence ${match[1]}%`;
  match = text.match(/^连接目标线段 (\d+) 等分点$/); if (match) return `Connect to a ${match[1]}-division point on target segment`;
  match = text.match(/^(.+) · 等待调整四角$/); if (match) return `${match[1]} · ready for corner adjustment`;
  match = text.match(/^(\d+) 左上 · 局部放大$/); if (match) return `${match[1]} top-left · zoom`;
  match = text.match(/^(\d+) 右上 · 局部放大$/); if (match) return `${match[1]} top-right · zoom`;
  match = text.match(/^(\d+) 右下 · 局部放大$/); if (match) return `${match[1]} bottom-right · zoom`;
  match = text.match(/^(\d+) 左下 · 局部放大$/); if (match) return `${match[1]} bottom-left · zoom`;
  match = text.match(/^有 (\d+) 个图像线段偏离 22\.5° 系，已忽略。$/); if (match) return `${match[1]} image segments deviated from the 22.5° family and were ignored.`;
  match = text.match(/^新增后仍有 (\d+) 个 cAMV 结构可疑节点；该指标没有被当作绝对否决条件。$/); if (match) return `${match[1]} cAMV structure-suspect nodes remain after the additions; this metric is not treated as an absolute veto.`;
  match = text.match(/^本版本在严格 22\.5° 结果上新增 (\d+) 条“(.+)”精确构造；每条线都由残余笔画像素触发，并保留父规则与证据，不是自由角度拟合。$/); if (match) return `This version adds ${match[1]} exact “${translateEnglish(match[2])}” constructions to the strict 22.5° result. Each is triggered by residual stroke evidence and retains its parent rule and evidence; none is a free-angle fit.`;
  match = text.match(/^cAMV 结构复核触发强证据补线：经过 (\d+) 轮，补回 (\d+) 条精确节点间射线，结构异常由 (\d+) 降至 (\d+)。$/); if (match) return `cAMV structure review triggered strong-evidence completion: after ${match[1]} rounds, ${match[2]} exact node-to-node rays were restored and structure anomalies fell from ${match[3]} to ${match[4]}.`;
  match = text.match(/^cAMV 结构子集发现 (\d+) 个可疑节点（奇数折痕 (\d+)，川崎角度 (\d+)，边界拓扑 (\d+)）。这是高权重完备性信号，但不会单独否决结果。$/); if (match) return `The cAMV structure subset found ${match[1]} suspect nodes (odd fold count ${match[2]}, Kawasaki-angle ${match[3]}, boundary topology ${match[4]}). This is a high-weight completeness signal, but it does not reject a result by itself.`;
  match = text.match(/^完整 cAMV 仍有 (\d+) 个异常节点（结构 (\d+)，Maekawa (\d+)，big-little-big (\d+)）。程序不会翻转强红\/强蓝折痕来强行消除这些异常。$/); if (match) return `Full cAMV still reports ${match[1]} anomalous nodes (structure ${match[2]}, Maekawa ${match[3]}, big-little-big ${match[4]}). Strong red/blue crease evidence is never flipped merely to eliminate these anomalies.`;
  return text;
}

function translateDynamic(text) { if (!text) return text; return language === 'zh' ? text.replaceAll('重建', '重绘') : translateEnglish(text); }
function translateTextNode(node) {
  if (!node?.nodeValue || node.parentElement?.closest('script, style')) return;
  const raw = node.nodeValue, trimmed = raw.trim(); if (!trimmed) return;
  const translated = translateDynamic(trimmed); if (translated !== trimmed) node.nodeValue = raw.replace(trimmed, translated);
}
function translateTree(root) {
  if (root.nodeType === Node.TEXT_NODE) { translateTextNode(root); return; }
  if (root.nodeType !== Node.ELEMENT_NODE && root !== document) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let node;
  while ((node = walker.nextNode())) translateTextNode(node);
}

applyStaticTranslations();
translateTree(document.body);
const observer = new MutationObserver(records => {
  for (const record of records) {
    if (record.type === 'characterData') translateTextNode(record.target);
    for (const node of record.addedNodes) translateTree(node);
  }
});
observer.observe(document.body, { childList: true, subtree: true, characterData: true });

document.querySelector('#language-toggle')?.addEventListener('click', () => {
  localStorage.setItem(STORAGE_LANGUAGE, language === 'zh' ? 'en' : 'zh');
  location.reload();
});
const welcomeDialog = document.querySelector('#welcome-dialog');
document.querySelector('#welcome-close')?.addEventListener('click', () => welcomeDialog?.close());
welcomeDialog?.addEventListener('close', () => localStorage.setItem(STORAGE_WELCOME, '1'));
welcomeDialog?.addEventListener('click', event => { if (event.target === welcomeDialog) welcomeDialog.close(); });
if (welcomeDialog && !localStorage.getItem(STORAGE_WELCOME)) welcomeDialog.showModal();
