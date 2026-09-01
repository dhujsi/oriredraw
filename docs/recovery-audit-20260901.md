# 恢复审计记录（2026-09-01）

## 结论

当前仓库中，`c4ce919` 是 2026-08-27 之后唯一存在的正式提交。之后没有新的提交、远程分支提交或可回收的悬空提交。

因此，当前工作区相对 `c4ce919` 的修改可以完整比较，但不能从 Git 还原每一次中间状态。

## 已确认的记录

- `main` 和 `origin/main` 当前都指向 `c4ce919`。
- `c4ce919` 的提交说明是 `feat: add guided raw CP reconstruction`。
- `git log --all` 没有 2026-08-27 之后的新提交。
- `git reflog --all` 没有更晚的提交或回退点。
- `git fsck --full --unreachable --no-reflogs` 没有发现悬空提交。
- 当前工作区相对 `c4ce919` 有 18 个已修改文件，全部未提交。
- 当前任务没有挂载 Codex 终端会话；PowerShell 历史只记录普通终端命令，不记录 Codex 内部 `apply_patch` 调用。

## 当前工作区相对基线的文件差异

```text
finite_endpoint_closure.py
guided_construction.py
guided_cp_output.py
raw_crease_evidence.py
raw_crease_topology.py
raw_primary_bridge.py
reconstructor.py
tests/test_finite_endpoint_closure.py
tests/test_guided_cp_output.py
tests/test_guided_primary_web.py
tests/test_raw_crease_evidence.py
tests/test_raw_crease_topology.py
tests/test_raw_primary_bridge.py
web/app.js
web/i18n.js
web/index.html
web/project-core.js
web/style.css
```

## 已确认的修改内容

以下内容来自当前任务上下文和当前文件 diff；不是从一个完整的历史补丁日志读取出来的，因此不宣称能还原原始命令顺序：

1. 原图几何识别改为不依赖红蓝颜色，几何阶段同时处理不同颜色的线。
2. 峰谷处理改为：清楚红蓝作为证据，内部节点按 Maekawa 规则逐轮推断，最后无法确定的线才设为红色。
3. 新增的 Maekawa 推断来源已接入 Python `.cp` 输出和网页端信任来源。
4. 增加了对应的单元测试和网页静态检查。

## 可复现对照

使用同一份霸王龙原图 `0487ddf5c5ae7516`：

| 代码 | 原图线 | 拓扑点 | 拓扑线段 |
|---|---:|---:|---:|
| `c4ce919` 隔离副本 | 91 | 104 | 169 |
| 当前未提交工作区 | 95 | 137 | 167 |
| `霸王龙-一次起点-当前项目-20260901.oriredraw` 保存结果 | 91 | 104 | 169 |

这证明恢复目标应以保存项目的 91/104/169 结果为验收样本，不能把当前未提交工作区直接当作上版代码。

## 恢复规则

恢复时不得使用 `git reset --hard` 覆盖当前工作区。应保留当前工作区副本，在隔离目录以 `c4ce919` 为基线，逐块回退导致几何结果从 91/104/169 变成 95/137/167 的改动，并在每个子任务暂停点记录：修改文件、验证命令、结果、下一节点和恢复方法。
