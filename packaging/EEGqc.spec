"""One Windows directory build; use the locked project environment."""

from pathlib import Path

import eegprep
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
exports = (
    "pop_loadset", "pop_saveset", "pop_select", "pop_rejepoch", "pop_subcomp",
    "pop_eegfiltnew", "eeg_interp", "topoplot", "pop_runica", "iclabel",
    "ICL_feature_extractor", "eeg_rpsd", "eeg_autocorr", "eeg_autocorr_welch",
    "eeg_autocorr_fftw", "pop_reref",
)
# EEGPrep exposes these through importlib instead of ordinary Python imports.
hidden = [eegprep._LAZY_EXPORTS[name][0] for name in exports]
hidden += collect_submodules("eegqc", filter=lambda n: n != "eegqc.epochlabel.server")
hidden += collect_submodules("mne", filter=lambda n: ".tests" not in n and not n.startswith("mne.commands"))
datas = collect_data_files("eegprep") + collect_data_files("mne")
# Keep the distributed runtime versions and their bundled license material,
# including Qt/PySide and CPU ICLabel's dependencies, with the application.
datas += copy_metadata("eegqc", recursive=True) + copy_metadata("torch", recursive=True)
datas += [(str(root / "THIRD_PARTY_NOTICES.md"), "eegqc")]

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "pytest", "IPython", "notebook", "uvicorn", "fastapi"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="EEGqc", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="EEGqc")
