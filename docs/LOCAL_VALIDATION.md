# 真实数据试用准备记录

日期：2026-10-30；源码：main 的 01c82e2，运行版本 0.3.1。本轮未修改运行代码。

## 环境与基线

Windows 11（10.0.26200），Intel64 Family 6 Model 198 Stepping 2，Python 3.12.10；使用 uv.lock 安装，EEGPrep 0.4.0、NumPy 2.5.4、SciPy 1.18.1、PySide6 6.12.0、threadpoolctl 3.6.0，含 ICLabel CPU 依赖。

Ruff 与 51 个文件的格式检查通过；pytest 为 44 passed（20.47 秒）。现有 Starlette 和 EEGPrep 弃用提示保留，未修改上游代码。

## 样例与数据闭环

| 本机样例 | 布局 | 验证结果 |
|---|---|---|
| sample_data/eegprep/ | 公开 32 通道、80 epochs、384 点、128 Hz，已有 ICA | v5/.fdt 与 HDF5 信号一致；IC 排除与插值结果对 EEGPrep 最大差 0.0000252 µV；ERP、trial 映射、导出与恢复通过 |
| sample_data/physionet/ | 公开 64 通道、160 Hz、125 秒；另有 125 个固定一秒窗口，无来源 ICA | 30 个原注释及导出映射通过；连续删除一秒后保留 19840 点；固定窗口导出保留 123 段；最大回读差 1.08e-19 V |
| sample_data/local-reference/recording-a.set | 327 epochs × 59 通道 × 375 点、250 Hz；57 个 ICA 通道、52 个成分 | 桌面 QC、新 ICA、ICLabel、ERP、保存与新进程恢复通过；导出 326 epochs，最大差 1.36e-20 V |
| sample_data/local-reference/recording-b.set | 263 epochs × 128 通道 × 750 点、250 Hz；118 个 ICA 通道、105 个成分 | 同一完整流程通过；导出 262 epochs，最大差 1.08e-19 V |

两份本机研究样例从相邻 ManualQC 的已有文件复制，使用本地别名；来源映射保留在 sample_data/ 的本地说明中。未发布研究数据、自由文本、评分文件或对应截图。两份研究文件均成功导入事件条件；没有额外行为字段，未推断行为条件含义。

桌面流程调用现有 release_check：输入标记、来源 IC 排除与通道插值、采用后旧判断待复核、重新确认、从当前状态拟合 8 个新 IC、候选预览和排除、采用、epoch 标记、实际 ICLabel、可见 ERP 对直接平均、保存、导出、全新 Python 进程恢复矩阵并再次导出。每个新分解均得到 8×7 有限概率。32 次拟合迭代用于验收，不作为分离质量或分类准确率证据。

59 通道流程同时接入公开 64 通道连续数据，实际完成 ASR 中浏览、取消、重跑、采用和一秒时间段删除，回读保留 19840 点。软件验证中的刻意标记不计为研究人工标签。

本机报告：sample_data/eegprep/validation.json、sample_data/physionet/provenance-validation.json、output/local-layouts.json、output/local-128/{acceptance,reopen}.json、output/local-59-continuous64/{acceptance,reopen}.json。实际 Qt 截图随对应报告保存在 output/，已检查 128 通道的波形、状态摘要与 ERP 显示。

公开数据来源、许可和转换方法沿用[原验收记录](V0_3_ACCEPTANCE.md)；PhysioNet 固定窗口仅用于兼容性验证，不是实验 trials，模板坐标不是个体测量位置。

## 本机交互计时

现有 benchmarks/gui_preview.py，一次预热、每项 30 次，窗口 1500×1000、32 个可见通道、ERP 可见、两个 BLAS 池均为 2 线程。计时到 Qt offscreen widget 绘制完成，不包括操作系统合成与显示器延迟。插值使用已建立的权重。

| 数据 | 翻页 p95 | IC 与 ERP p95 | 插值与 ERP p95 | epoch 与 ERP p95 |
|---|---:|---:|---:|---:|
| 59 通道 / 327 epochs | 40.8 ms | 42.0 ms | 41.5 ms | 45.0 ms |
| 128 通道 / 263 epochs | 50.3 ms | 46.8 ms | 47.8 ms | 46.0 ms |
| 0.3 目标 | ≤100 ms | ≤150 ms | ≤200 ms | ≤100 ms |

59 通道准备数据 905 ms、随后首次视图 602 ms；128 通道分别为 1458 ms、696 ms。冷准备未混入暖缓存指标。RSS 从 301.79/384.06 MB 增至 316.29/430.58 MB；128 通道插值第 5/15/30 次为 429.42/429.47/429.85 MB，后续 epoch 操作为 429.52/429.49/429.70 MB。此序列缓存填充后无持续增长趋势，不据此宣称所有长时工作负载的内存恒定。

原始报告：output/gui-local-59.json、output/gui-local-128.json。

## 尚待实际材料

当前证据覆盖真实文件上的程序化工作流和数值一致性。研究者的连续独立试用、EpochLabel 多评分者采集和独立模型评价仍待开展，见[后续计划](NEXT_STEPS.md)。
