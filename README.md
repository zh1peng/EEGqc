# EEGqc

以 EEGPrep 为主要科学后端的 EEG 人工质控桌面应用。当前开发版本为 **0.2.0**，支持候选处理预览、采用后继续 QC、条件 ERP 和本地 EpochLabel 采集流程。参见[开发方案](DEVELOPMENT_PLAN.md)与[实现及验证记录](docs/IMPLEMENTATION_STATUS.md)。

## 运行

```powershell
uv sync
uv run eegqc --demo
uv run eegqc path/to/recording.set
```

需要 Python 3.12 或更新版本；uv 可准备项目环境。EEGPrep 固定为 0.4.0，依赖由 uv.lock 记录。界面使用 PySide6-Essentials 与 PyQtGraph，无需 WebEngine。首次使用 ICLabel 或贡献接收服务时安装对应可选依赖：

```powershell
uv sync --extra iclabel --extra server
uv run --extra iclabel eegqc path/to/recording.set
```

后续运行 ICLabel 时继续保留 `--extra iclabel`；`uv run` 默认同步可能移除未选中的可选依赖。

## 质控流程

四个工作区分别用于 Epoch、通道、IC 检查与结果复核，ERP 面板可停靠，布局可保存、载入和恢复。

- 排除已有 IC、球面坏道插值、导出删除通道、平均参考先形成候选预览。点击“采用预览并继续 QC”后，人工判断绑定该处理状态。
- 可将输入或任意已采用状态固定为参考，叠加比较波形；条件 ERP 可分别比较处理效果、epoch 剔除或整体 QC。
- ERP 支持元数据多字段 AND/OR 条件、数值比较、集合选择与命名条件叠加；条件预设随会话保存。
- 通道属性包含 EEGPrep PSD、epoch 图与头皮位置；IC 地图使用 EEGPrep topoplot，属性包含来源分解的时序和投影 PSD。
- “视图 → 显示滤波”同时作用于波形、属性与 ERP，并在数据栏显示设置。它不改变导出数据；人工标签记录实际看见的显示设置。
- 连续数据可拖动蓝色范围标记坏时间段、清除重叠标记、前后翻页；导出通过 EEGPrep 删除相应采样，保留边界事件与来源区间映射。
- 振幅阈值、ICLabel 与试验 EpochLabel 模型输出候选建议，最终由用户确认。
- 支持目录正则搜索、批次导航、评级、备注、自动保存、撤销和重做。B 标记/取消，方向键翻页，Ctrl+Z 撤销；B 与方向键在波形/导航区域获得焦点时启用。

输入文件不会被改写。“保存记录”写入 JSON 会话，“导出 .set”生成清理后的数据；CSV 只包含已检查的 epoch 标签。实际文件的修改会自动保存到同目录 .eegqc.json。恢复只检查路径、大小、修改时间与布局，不执行全量哈希。

## ICLabel 与 ASR

“算法 → ICLabel 分类与选择”对输入的已有 ICA 分解给出七类概率，可按类别及阈值选择 IC 并预览。公开 EEGPrep 样例已实际运行 CPU 推断；缺少 ICA 的文件不能直接分类。

“算法 → ASR 连续数据预览”允许选择 cutoff、校准时间段与处理前高通。ASR 在后台生成新阶段；校准区间必须在一个连续片段内，已有 boundary 会重置处理状态。独立 epochs 不拼接成连续数据进行 ASR。ASR 后旧 ICA 不再用于新阶段；重新拟合 ICA 尚未实现。

EEGPrep 0.4.0 的 ASR 在公开 32 通道样例上出现了屏蔽子空间的伪逆数值问题。EEGqc 保留其校准与处理逻辑，仅对重建伪逆使用 SciPy 的维度缩放容差，并在记录中保存实现版本。来源、许可及独立数值对照见 [ASR_NUMERICS.md](docs/ASR_NUMERICS.md)。

## EpochLabel 采集与研究基线

“文件 → 导出 EpochLabel 贡献包”会展示匿名分组和可选条件字段，并要求明确勾选同意。包内仅含已人工检查的 epochs；可选择是否包含波形。每段波形重建为当时判断所用的处理状态，显示滤波另存为判断上下文。文件路径、自由文本备注和外部原始 TrialID 不进入贡献包。条件元数据可包含敏感值，应在对话框中选择需要的字段。

匿名评分者 ID 保存在本机；lab、study、participant、recording ID 由项目统一分配，同一被试跨文件保持一致。未检查数据不作为好样本，行为排除和不确定标签不直接作为伪迹训练目标。

应用支持上传已导出的包和按回执撤回。上传使用独立后台队列，令牌不写入回执。项目本地接收端运行方式：

```powershell
$env:EEGQC_CONTRIBUTION_TOKEN = "<项目贡献令牌>"
uv run --extra server eegqc-collect --directory contributions --host 127.0.0.1 --port 8765
```

上传地址填写接收端根地址，例如 http://127.0.0.1:8765。回执保存为贡献包旁的 .receipt.json；撤回操作删除该接收端中的原始贡献包，不会回滚已经训练或另行导出的模型。仓库尚未部署公共收集服务。

已有可解释特征与逻辑回归研究基线，按被试、研究或实验室留出验证；标准化只拟合训练集，重复贡献按同一评分者的最新判断去重。训练工具输出可读 JSON 模型与评价结果：

```powershell
uv run eegqc-epochlabel-train data/participant-a.zip data/participant-b.zip --output epochlabel-baseline.json --group-by participant
```

需要足够独立分组且训练、验证两侧均有好/坏标签。“算法 → EpochLabel 试验模型建议”可载入该 JSON，输出候选而不自动标记。当前没有经过多实验室独立验证的通用 EpochLabel 模型。

## 验证

```powershell
uv run pytest
uv run ruff check src tests tools benchmarks
uv run python benchmarks/preview.py --channels 128 --epochs 400
```

测试针对科学结果和状态一致性，不以启动 smoke test 或覆盖率为目标。公开样例的独立验证与实际 Qt 截图可通过以下工具复现；pytest 本身不联网：

```powershell
uv run python tools/validate_reference.py --download --hdf5
uv run python tools/validate_asr_reference.py --download
uv run python tools/capture_demo.py
uv run python tools/capture_workflows.py
uv run python tools/capture_asr.py
```

结果位于 output/。已经验证 v5 .set + 外置 .fdt 与对应 HDF5 样例。仍需推进用户实际文件的兼容性与完整交互延迟验收、ICA 重拟合、桌面安装包，以及多评分者数据采集和独立模型评价。
