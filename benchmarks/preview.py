"""Representative core timings. Does not claim end-to-end GUI paint latency."""

import argparse
import json
import platform
from time import perf_counter

import numpy as np
import psutil
import scipy.linalg  # noqa: F401 -- load SciPy's BLAS before applying thread limits
from threadpoolctl import threadpool_info, threadpool_limits

from eegqc.core.preview import PreviewEngine
from eegqc.core.state import Processing
from eegqc.demo import make_demo


def measure(function, repeats=30):
    function()
    samples = []
    for _ in range(repeats):
        start = perf_counter()
        function()
        samples.append((perf_counter() - start) * 1000)
    return {
        "p50_ms": round(float(np.percentile(samples, 50)), 3),
        "p95_ms": round(float(np.percentile(samples, 95)), 3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channels", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--sfreq", type=float, default=500.0)
    parser.add_argument("--duration", type=float, default=1.2)
    args = parser.parse_args()
    with threadpool_limits(limits=2, user_api="blas"):
        ds = make_demo(args.channels, args.epochs, args.sfreq, args.duration)
        engine = PreviewEngine(ds)
        proc = Processing(excluded_ics=(0, 3), interpolated=(0,))
        start = perf_counter()
        engine.erp(proc, condition=("condition", "target"))
        cold = (perf_counter() - start) * 1000
        toggled = False
        ic_toggled = False

        def toggle_epoch():
            nonlocal toggled
            toggled = not toggled
            return engine.erp(
                proc, frozenset({0}) if toggled else frozenset(), condition=("condition", "target")
            )

        def toggle_ic():
            nonlocal ic_toggled
            ic_toggled = not ic_toggled
            processing = proc if ic_toggled else Processing(excluded_ics=(0,), interpolated=(0,))
            return engine.erp(processing, condition=("condition", "target"))

        result = {
            "shape": list(ds.data.shape),
            "input_mb": round(ds.data.nbytes / 1e6, 2),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "blas": threadpool_info(),
            "first_erp_ms": round(cold, 3),
            "window_5_epochs_32_channels": measure(
                lambda: engine.signal(proc, slice(0, 5), range(min(32, ds.n_channels)))
            ),
            "erp_epoch_toggle": measure(toggle_epoch),
            "erp_ic_toggle": measure(toggle_ic),
        }
        memory = psutil.Process().memory_info()
        result["resident_memory_mb"] = round(memory.rss / 1e6, 2)
        result["peak_working_set_mb"] = round(getattr(memory, "peak_wset", memory.rss) / 1e6, 2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
