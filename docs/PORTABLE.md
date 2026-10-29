# EEGqc Windows 便携版

完整解压 zip，双击 EEGqc 文件夹内的 EEGqc.exe。保留同目录的 _internal 文件夹，运行无需安装 Python。文件菜单可打开 .set，或加载明确标记的合成演示数据。

科学处理先形成候选，点击“采用预览并继续 QC”后再标记。保存记录用于关闭后恢复，导出 .set 用于生成清理后的数据。输入数据和会话记录放在有写权限的数据目录；便携程序可以放在其他位置。

长任务面板显示当前阶段和已用时间，取消后不会采用未完成结果。重新 ICA 的分解和它所需的小型数组与会话文件共同保存，移动记录时需一并保留。

通过命令行打开数据或演示：

```powershell
.\EEGqc.exe "D:\研究数据\recording.set"
.\EEGqc.exe --demo
```

ICLabel 的模型资源由发行包提供，不在质控时联网下载。贡献上传由用户明确发起；贡献服务器和训练工具通过源码项目提供。

开发者使用 tools/build_portable.ps1 和 uv.lock 构建同一种目录式发行包。发行验收使用 --verify-release 对公开样例执行实际 QC、保存恢复、导出回读及子进程取消，并将 JSON 报告写到指定目录；该入口不属于日常质控流程。

```powershell
.\EEGqc.exe --verify-release --dataset "D:\公开样例\eeglab_data_epochs_ica.set" --output "D:\验收结果"
```

当前发行覆盖的数据、计算容差和运行环境见仓库的 docs/IMPLEMENTATION_STATUS.md 与后续版本验收记录。实际使用中遇到错误时，报告具体操作、输入布局和错误信息；研究数据可按项目规则另行提供。
