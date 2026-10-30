# 0.3.1 后续试用与研究计划

更新：2026-10-30。0.3 的 A1–A4、B1–B2 已交付，原计划见 [0.3 开发计划](DEVELOPMENT_V0_3.md)，本机补充验证见 [真实数据试用准备记录](LOCAL_VALIDATION.md)。

## 已完成的准备

- 本机按 uv.lock 建立 Python 3.12 环境，包含 ICLabel；44 项测试、Ruff 与格式检查通过。
- 公开 32 通道 v5/.fdt 与 HDF5 对照、64 通道连续及固定窗口导出验证通过。
- 两份实际研究文件（59 通道/327 epochs、128 通道/263 epochs）完成桌面 QC、新 ICA、ICLabel、保存、全新进程恢复和导出回读。
- 两份研究文件完成 32 个可见通道、ERP 可见的交互计时，四类操作均达到 0.3 目标。
- 已验收的原始便携包发布到 [0.3.1 Release](https://github.com/zh1peng/EEGqc/releases/tag/v0.3.1)，固定下载不依赖 Actions 产物保留期。
- EpochLabel 已备好采集协议、60 个未标注样本的本地检查顺序和 6 个盲态重复项，等待独立人工评分。
- 源码中新增独立的 EpochRank 动态排序原型：相似坏样本与累计好坏反馈两个基线，见 [算法与验证说明](EPOCHRANK.md)。该原型不包含在已发布的 0.3.1 便携包中。
- 样例放在 sample_data/，运行结果放在 output/，均不提交到源码仓库。研究文件仅在本机使用。

## 下一步与结束条件

| 优先级 | 工作 | 结束条件 |
|---|---|---|
| 1 | 研究者实际试用 | 研究者在自己的 QC 流程中完成至少一份数据的检查、处理、保存、关闭恢复和导出，记录阻塞步骤与预期结果 |
| 2 | 修复试用问题 | 按数据错位、状态归属、工作丢失、导出差异和交互阻塞排序；每个问题以实际复现与相关回归验证结束 |
| 3 | EpochLabel 小规模采集 | 按[试点协议](EPOCHLABEL_PILOT.md)取得独立人工标签，核对信号状态、匿名分组与分歧；目前尚无采集材料 |
| 4 | EpochLabel 基线评价 | 有足够独立被试且训练/测试均包含好坏样本后，再运行已有逻辑回归基线；报告留出表现与不确定样本处理 |

实际研究者试用与多人标签尚未完成，目前无法安排评分者；两个未分配席位的独立数据、身份目录启动方式与检查顺序已准备。缺少真实标签时，后续模型评价不能开展。EpochRank 的软件实现与交互验证可独立推进；它能从用户在当前记录中的首个坏样本开始，但其效果比较仍需真实标签。自动执行的 QC 编辑只用于软件验证，不计入人工研究标签。已有验收使用 8 通道、32 次迭代的新 ICA 来检查软件流程，不证明整份数据的 ICA 分离质量。

## 本机复现

```powershell
uv sync --locked --extra iclabel
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests tools benchmarks packaging/launcher.py
uv run --no-sync ruff format --check src tests tools benchmarks packaging/launcher.py

uv run --no-sync eegqc sample_data/local-reference/recording-b.set
uv run --no-sync python -m eegqc.release_check --dataset sample_data/local-reference/recording-b.set --output output/local-128
uv run --no-sync python benchmarks/gui_preview.py --dataset sample_data/local-reference/recording-b.set --visible-channels 32 --output output/gui-local-128.json
```

本机未将 uv 所在目录加入 PATH 时，用 `python -m uv` 代替 `uv`。源码启动会在数据旁自动保存会话，移动会话时一并保留其引用的 NPZ。

只对实际修改或新疑点运行相应验证。当前结果没有要求新增计算后端、通用任务平台、数据哈希或按钮级 smoke tests。
