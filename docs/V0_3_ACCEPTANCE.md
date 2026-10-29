# EEGqc 0.3 验收记录

2026-10-30。目标范围来自 [下一阶段开发计划](DEVELOPMENT_V0_3.md)。当前代码版本为 0.3.1，合并了 0.3.0 的流程完善与 0.3.1 的 ICA 增强。本记录同时列出完成证据和仍在执行的发行验收。

## 工作包与证据

| 工作包 | 当前结果 | 证据 |
|---|---|---|
| A1 状态与判断 | 已实现并验证 | 当前/候选/参考摘要、旧判断待复核、参考恢复与撤销；tests/test_ui_workflow.py、tests/test_ica.py；真实公开数据源码验收 |
| A2 数据与性能 | 数据闭环已验证，最终 GUI 计时待记录 | EEGPrep 32 通道 v5/.fdt 与 HDF5；PhysioNet 64 通道 EDF 转换及连续/固定窗口 .set；benchmarks/gui_preview.py |
| A3 长任务 | 已实现并验证 | 独立 spawn 进程、真实阶段、取消/回收、输入固定、成功后发布导出；tests/test_long_tasks.py；真实 ASR 浏览/取消/重跑 |
| A4 界面与发行 | 界面验证完成，便携包与独立 Windows 验收进行中 | 1366×900 波形与 ERP、独立属性队列、保留缩放；packaging/、tools/build_portable.ps1、Windows portable acceptance 工作流 |
| B1 新 ICA | 已实现并验证 | 当前状态 Extended Infomax、通道/样本/秩/PCA、候选采用；原有和部分通道新 ICA 的 ICLabel 实际推断 |
| B2 保存与导出 | 已实现并验证 | schema v2 + 不可执行 npz、v1 导入、保存失败保留旧 JSON、数组复用；全新进程恢复后导出回读 |

## 科学和状态检查

本轮集成测试结果为 **43 passed**。Ruff 检查通过。新增验证针对新分解归属、数值重建、保存恢复、部分通道分类输入、数组重复写入和取消竞态，不采用按钮数量或覆盖率目标。

真实公开 EEGPrep 样例为 80 epochs × 32 通道 × 384 samples，128 Hz。源码验收实际执行：

输入标坏 → 排除来源 IC 并插值 → 采用后旧判断待复核 → 重新确认 → 在当前状态的 8 个 EEG 通道上拟合 ICA → 查看候选并排除新 IC → 采用及标记 → 显示 ERP → 保存 → 导出 → 启动全新 Python 进程恢复并再次导出。

新分解实际生成 8×7 ICLabel 有限概率。预览 ERP 与直接平均、基线一致；全新进程恢复后导出 79 epochs，最大信号差约 **2.71e-20 V**。这是实现一致性的证据，32 次迭代的验收拟合不作为 ICA 分离质量或分类泛化效果的研究结论。报告位于 output/release-source/acceptance.json 与 reopen.json。

连续公开样例同时执行实际 ASR：计算期间浏览，取消不采用结果，随后重跑成功并采用，再删除一秒时间段并回读导出。该验证使用相同科学内核，没有采用替代算法。原 ASR 数值兼容修正仍见 [ASR_NUMERICS.md](ASR_NUMERICS.md)。

### 部分通道 ICLabel

真实文件联调发现，若把部分通道 ICA 与整份记录一起交给 EEGPrep ICLabel，其重参考会因通道不匹配清除分解。本版本将分类副本限定到 ICA 的通道、顺序与位置，再由后端执行实际参考转换；工作信号保持不变。

公开样例的来源分解得到 32×7 概率，新拟合 8 个 EEG 通道的分解得到 8×7；各行和约为 1。已保存 output/reference/iclabel-partial-validation.json。分类副本的通道、矩阵、活动及参考语义有独立回归验证。

### 会话数组保存

仅新增科学依赖时写入新的 npz。标签、备注或评分改变时，通过普通分解 ID/处理状态集合复用现有不可变数组包，避免在自动保存中重写大段 ASR 信号。恢复后继续复用；新增分解或 ASR 阶段才新写，旧备份引用的包仍保留。

## 64 通道公开数据

使用 [PhysioNet EEG Motor Movement/Imagery Dataset](https://physionet.org/content/eegmmidb/1.0.0/) 的 S001R03.edf，许可为 [ODC Attribution v1.0](https://physionet.org/content/eegmmidb/view-license/1.0.0/)。文件为 64 通道、160 Hz、125 秒，包含 30 个原始 T0/T1/T2 注释。

tools/prepare_public_reference.py 明确通过 --download 下载一次；MNE 读取 V 单位信号并标准化通道名，使用 standard_1005 模板坐标，再转换为 EEGPrep .set。没有滤波、重参考或预先 ICA。生成原连续文件及 125 个固定一秒窗口，用于兼容性和性能测量；固定窗口不是实验 trials，其平均不宣称为有效实验 ERP，模板位置不代表个体定位。

两种文件均完成 Fp1 插值、人工标记、导出回读。最大信号差约 **1.08e-19 V**，原注释、时间段映射、来源 window ID 和条件元数据检查通过。来源、许可、转换及数据布局保存在 output/public-reference/provenance-validation.json、layout.json。

目前真实公开验证覆盖 32、64 通道；128 通道、400 epochs 使用明确标记的合成数据。用户自己的 128 通道研究文件仍按计划的数据范围条款后续验证，不把合成压力测量当作该项目真实文件验收。

## 交互计时

benchmarks/gui_preview.py 从应用操作入口开始计时，等待包含新版本数据的波形和可见 ERP 的 QWidget paint 完成；不包含系统 compositor 或显示器延迟。每项一次预热、30 次采样，记录数据、线程数、窗口尺寸、p50/p95 和内存。最终结果将在正式计时后填入。

## Windows 发行

使用锁定的 PyInstaller 6.22.3 构建单一目录式发行包，包含 Python/Qt、科学计算库和 ICLabel CPU 运行时及模型。launcher 在导入 Qt 前执行 multiprocessing.freeze_support，避免冻结子进程重复打开 GUI。使用 [PyInstaller 规格文件](https://pyinstaller.org/en/stable/spec-files.html)管理资源及 EEGPrep 的动态导入，并遵循其[冻结多进程说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing)。

打包完成后，Windows portable acceptance 工作流会在另一个全新的 Windows runner 中只下载便携包和公开数据，不 checkout 项目、不安装项目依赖，并清空 Python 环境和开发工具 PATH。冻结应用通过 --verify-release 执行与源码相同的数据闭环、ICLabel、ASR 取消和全新应用进程恢复，将 JSON 与实际界面截图作为验收产物。此项尚在执行，未以源码测试通过代替冻结应用验证。

本轮没有增加原始数据哈希、启动时全量扫描、通用流程引擎或自动重试平台。普通 CI 限定为一个 Windows/Python 版本的 Ruff 与科学/状态测试，便携发行验收仅由明确的发行验证触发。
