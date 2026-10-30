# EEGqc 0.3 验收记录

本文保留原交付证据。后续本机真实 59/128 通道数据闭环与性能结果见[补充验证](LOCAL_VALIDATION.md)，当前待办见[后续计划](NEXT_STEPS.md)。已验收的原始便携包现通过 [0.3.1 Release](https://github.com/zh1peng/EEGqc/releases/tag/v0.3.1) 固定分发。

2026-10-30。目标范围来自 [下一阶段开发计划](DEVELOPMENT_V0_3.md)。A1–A4、B1–B2 已完成，交付版本为 0.3.1，合并了 0.3.0 的流程完善与 0.3.1 的 ICA 增强。科学计算与桌面发行对应提交 7bcedd47f920e6f283b88125c518430c52686418；后续验收文档提交不改变运行代码。

## 工作包与证据

| 工作包 | 当前结果 | 证据 |
|---|---|---|
| A1 状态与判断 | 已实现并验证 | 当前/候选/参考摘要、旧判断待复核、参考恢复与撤销；tests/test_ui_workflow.py、tests/test_ica.py；真实公开数据源码验收 |
| A2 数据与性能 | 已按计划的数据替代条款验证 | EEGPrep 32 通道 v5/.fdt 与 HDF5；PhysioNet 64 通道连续/固定窗口 .set；合成 64/128 通道 GUI 计时与内存记录；真实 128 通道项目文件仍待提供 |
| A3 长任务 | 已实现并验证 | 独立 spawn 进程、真实阶段、取消/回收、输入固定、成功后发布导出；tests/test_long_tasks.py；真实 ASR 浏览/取消/重跑 |
| A4 界面与发行 | 已实现并验证 | Windows 100%/125%/150% 逻辑工作区、独立属性队列、保留缩放和用户停靠尺寸；packaging/、tools/build_portable.ps1、独立 Windows 工作流通过 |
| B1 新 ICA | 已实现并验证 | 当前状态 Extended Infomax、通道/样本/秩/PCA、候选采用；原有和部分通道新 ICA 的 ICLabel 实际推断 |
| B2 保存与导出 | 已实现并验证 | schema v2 + 不可执行 npz、v1 导入、保存失败保留旧 JSON、数组复用；全新进程恢复后导出回读 |

## 科学和状态检查

本轮集成测试结果为 **44 passed**，见[最终代码的 Windows CI](https://github.com/zh1peng/EEGqc/actions/runs/40927065390)。Ruff 检查及 51 个文件的格式检查通过。新增验证针对新分解归属、数值重建、保存恢复、部分通道分类输入、数组重复写入、取消竞态，以及切换文件前最后一次修改的保存，不采用按钮数量或覆盖率目标。

真实公开 EEGPrep 样例为 80 epochs × 32 通道 × 384 samples，128 Hz。源码验收实际执行：

输入标坏 → 排除来源 IC 并插值 → 采用后旧判断待复核 → 重新确认 → 在当前状态的 8 个 EEG 通道上拟合 ICA → 查看候选并排除新 IC → 采用及标记 → 显示 ERP → 保存 → 导出 → 启动全新 Python 进程恢复并再次导出。

新分解实际生成 8×7 ICLabel 有限概率。预览 ERP 与直接平均、基线一致；全新进程恢复后导出 79 epochs，最大信号差约 **2.71e-20 V**。这是实现一致性的证据，32 次迭代的验收拟合不作为 ICA 分离质量或分类泛化效果的研究结论。报告位于 output/release-source/acceptance.json 与 reopen.json。

连续公开样例同时执行实际 ASR：计算期间浏览，取消不采用结果，随后重跑成功并采用，再删除一秒时间段并回读导出。该验证使用相同科学内核，没有采用替代算法。原 ASR 数值兼容修正仍见 [ASR_NUMERICS.md](ASR_NUMERICS.md)。

同版本的 v5/.fdt 与 HDF5 对照见 output/reference/validation.json：两种输入一致，清理结果与 EEGPrep 直接调用的最大差为 0.0000252 µV，来源 trial 映射和会话恢复通过。

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

[benchmarks/gui_preview.py](../benchmarks/gui_preview.py) 从应用操作入口开始计时，等待包含新版本数据的波形和可见 ERP 的 QWidget paint 完成；不包含系统 compositor 或显示器延迟。使用原生 Qt 事件循环接收绘制事件，每项一次预热、30 次采样。最终数据为 output/gui-v03-delivery-{128,64,public64,public32}.json。

环境为 Windows 10、Intel Family 6 Model 60、Python 3.12、NumPy 2.5.4、SciPy 1.18.1、PySide6 6.12.0，两个 OpenBLAS 线程池均限制为 2 线程。窗口 1500×1000，32 个可见通道、ERP 可见，Qt offscreen 绘制。

下表各格为 **p50 / p95，单位 ms**。插值项使用已经建立的权重；公开 64 通道文件没有预先 ICA，因此该项不适用。

| 数据 | 翻页 | IC 波形和 ERP | 插值波形和 ERP | epoch 拒绝/恢复和 ERP |
|---|---:|---:|---:|---:|
| 合成 128 通道 × 400 epochs × 600 点，500 Hz | 69.5 / 72.5 | 71.1 / 73.3 | 71.4 / 81.8 | 72.8 / 77.6 |
| 合成 64 通道 × 300 epochs × 600 点，500 Hz | 69.3 / 79.1 | 69.0 / 70.9 | 67.7 / 81.0 | 69.5 / 71.9 |
| 公开 64 通道 × 125 固定窗口 × 160 点，160 Hz | 55.6 / 57.9 | 不适用 | 54.6 / 55.5 | 56.8 / 59.0 |
| 公开 32 通道 × 80 epochs × 384 点，128 Hz | 60.0 / 62.8 | 61.5 / 63.0 | 59.2 / 60.6 | 61.7 / 64.2 |

四项 p95 均满足计划的 100/150/200/100 ms 初始目标。数据准备耗时分别为 1961、802、1806、1816 ms，随后首次视图分别为 1363、1314、1275、1286 ms；这些冷启动工作没有混入暖缓存指标，也不代表所有算法的首次准备耗时。

128 通道进程 RSS 从 472.93 MB 增至 510.68 MB，主要发生在首次建立插值缓存。插值重复第 5/15/30 次分别为 510.05/509.49/510.27 MB，随后 epoch 操作为 509.55/509.58/509.71 MB；没有随切换次数持续增加的趋势。这是本序列内的观察，不宣称任意工作负载的内存恒定。64 通道最终为 339.35 MB，两个公开文件最终为 272.22/269.75 MB。

长任务另行记录输入传递：一次 40.96 MB 数组的序列化约 16 ms，启动及传递约 703 ms，结果传递约 93 ms，总计约 875 ms。因此本轮保留单个独立进程，没有另建共享内存管理器。

## 系统缩放与可用空间

在本机 Windows Qt 平台的独立进程中分别设置 QT_SCALE_FACTOR=1、1.25、1.5，使用真实屏幕工作区计算初始尺寸。报告位于 output/v03-scaling-scroll-{1,1_25,1_5}.json，附相应截图；这是系统缩放的程序化检查，不替代用户试用。

| 缩放 | 逻辑工作区 | 初始窗口 | 中央/右侧纵向可滚动范围 |
|---|---|---|---|
| 100% | 1920×1040 | 1500×990 | 0 / 0 |
| 125% | 1536×832 | 1500×782 | 90 / 83 |
| 150% | 1280×693 | 1260×643 | 229 / 222 |

各窗口均位于启动尺寸限制内。较小逻辑高度下通过滚动完整查看波形，实际完成标记、保存和恢复参考；ERP 保留至少 160 px 绘图区。用户调整后的 360 px 停靠面板在隐藏/重显和布局保存/恢复后保留，不在每次刷新时强制重置。

## Windows 发行

使用锁定的 PyInstaller 6.22.3 构建单一目录式发行包，包含 Python/Qt、科学计算库和 ICLabel CPU 运行时及模型。launcher 在导入 Qt 前执行 multiprocessing.freeze_support，避免冻结子进程重复打开 GUI。使用 [PyInstaller 规格文件](https://pyinstaller.org/en/stable/spec-files.html)管理资源及 EEGPrep 的动态导入，并遵循其[冻结多进程说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing)。

打包完成后，Windows portable acceptance 工作流在另一个全新的 Windows runner 中只下载便携包和公开数据，不 checkout 项目、不安装项目依赖，并清空 Python 环境和开发工具 PATH。冻结应用通过 --verify-release 执行与源码相同的数据闭环、ICLabel、ASR 取消和全新应用进程恢复，将 JSON 与实际界面截图作为验收产物。

最终提交的[发行验收](https://github.com/zh1peng/EEGqc/actions/runs/40927093685)中，构建和独立验收两个作业均通过。acceptance.json 的 status=passed、frozen=true、version=0.3.1；实际拟合 8 个新成分并得到 8×7 ICLabel 概率，连续数据浏览/取消/重跑/时间段导出均通过。新的 EEGqc.exe 进程直接恢复矩阵并导出 79 epochs，与保存前预期信号的最大差为 2.71e-20 V。离线演示为明确标记的 240×64×250 合成数据。中文及空格路径中的运行、保存与读取通过。

报告及截图保存在 output/ci-40927093685/，GitHub 同次运行的 portable-acceptance 产物也提供这些证据。可分发包为该次运行的 eegqc-windows-x64 产物，文件名 EEGqc-0.3.1-windows-x64.zip，317,178,822 bytes（约 317 MB），完整解压后约 785 MB；无需 Python。已核对 Qt 运行时、ICLabel 模型和运行库版本/许可材料随包提供。GitHub Actions 产物保留 7 天，本地 zip 位于 output/portable/，本地已解压入口为 output/portable/0.3.1/EEGqc/EEGqc.exe。

此前的真实冻结验收发现并修复了两项仅源码测试无法覆盖的问题：

- threadpoolctl 3.7 的 Windows 模块路径枚举在中文路径中产生不可加载的路径；锁定 3.6.0，实际 Unicode 路径和 BLAS 线程限制通过，没有吞掉错误或替换科学算法。
- 无控制台进程的 sys.stderr 为 None，EEGPrep 的日志初始化仍调用 isatty；冻结入口在创建子进程前把缺失的输出流接到本机 desktop.log，主进程和子进程均可正常初始化。

这些修正由最终代码的独立发行验收再次覆盖。发行包使用说明见 [PORTABLE.md](PORTABLE.md)。

本轮没有增加原始数据哈希、启动时全量扫描、通用流程引擎或自动重试平台。普通 CI 限定为一个 Windows/Python 版本的 Ruff 与科学/状态测试，便携发行验收仅由明确的发行验证触发。
