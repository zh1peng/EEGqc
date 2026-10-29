"""One cancellable scientific subprocess, independent of preview and network jobs.

Only trusted, module-level task functions are accepted. Inputs are serialized at
start; the child never receives a live Session or Qt object from the GUI process.
"""

from dataclasses import dataclass, field
import multiprocessing as mp
import os
from pathlib import Path
import pickle
from queue import Empty, Queue
import shutil
import tempfile
from threading import Event, Thread
from time import monotonic
import traceback
from uuid import uuid4


class TaskCancelled(Exception):
    pass


@dataclass(frozen=True)
class StagedOutput:
    source: Path
    destination: Path


@dataclass
class TaskRecord:
    kind: str
    data_id: str
    input_state: object
    parameters: dict
    task_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "running"
    stage: str = "启动子进程"
    started_at: float = field(default_factory=monotonic)
    finished_at: float | None = None
    result: object = None
    error: str | None = None
    timings: dict = field(default_factory=dict)

    @property
    def elapsed(self):
        return (self.finished_at or monotonic()) - self.started_at


class TaskContext:
    """Child-only progress/checkpoint interface; no fabricated percentages."""

    def __init__(self, connection, cancelled, work_dir):
        self._connection = connection
        self._cancelled = cancelled
        self.work_dir = Path(work_dir)

    def check_cancelled(self):
        if self._cancelled.is_set():
            raise TaskCancelled()

    def report(self, stage):
        self.check_cancelled()
        self._connection.send(("stage", str(stage)))

    def output(self, destination):
        """Use start(output_path=destination) to ensure same-volume publication."""
        destination = Path(destination).resolve()
        if destination.parent != self.work_dir.parent:
            raise ValueError("Export tasks require output_path=destination when starting")
        return StagedOutput(self.work_dir / destination.name, destination)


def _run_task(connection, cancelled, work_dir, payload, submitted_at):
    """Static Windows spawn/frozen entry point; imports no GUI modules."""
    try:
        received_at = monotonic()
        function, args, kwargs = pickle.loads(payload)
        context = TaskContext(connection, cancelled, work_dir)
        connection.send(
            (
                "ready",
                {
                    "spawn_transfer_seconds": received_at - submitted_at,
                    "deserialize_seconds": monotonic() - received_at,
                },
            )
        )
        context.check_cancelled()
        # Limit nested BLAS pools in each independent scientific process.
        from threadpoolctl import threadpool_limits

        with threadpool_limits(limits=2, user_api="blas"):
            result = function(context, *args, **kwargs)
        context.check_cancelled()
        connection.send(("result_started", monotonic()))
        connection.send(("result", result))
    except TaskCancelled:
        connection.send(("cancelled", None))
    except Exception as error:  # application boundary: preserve parent state
        connection.send(("error", f"{type(error).__name__}: {error}\n{traceback.format_exc()}"))
    finally:
        connection.close()


class LongTaskRunner:
    """Single owner API: start, poll, cancel, close. No Qt thread termination.

    A transport thread starts and drains the process pipe, so receiving a large
    ndarray does not stall Qt's event loop. Only poll commits a successful result.
    """

    def __init__(self):
        self.record = None
        self._context = mp.get_context("spawn")
        self._messages = Queue()
        self._thread = None
        self._stop = None
        self._cancelled = None
        self._work_dir = None

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, kind, function, *args, data_id, input_state, parameters=None, output_path=None, **kwargs):
        self.poll()
        if self.busy or (self.record is not None and self.record.status == "running"):
            raise RuntimeError("A long task is already running or being cancelled")
        record = TaskRecord(kind, data_id, input_state, dict(parameters or {}))
        before = monotonic()
        payload = pickle.dumps((function, args, kwargs), protocol=pickle.HIGHEST_PROTOCOL)
        record.timings.update(serialize_seconds=monotonic() - before, input_bytes=len(payload))
        parent = Path(output_path).resolve().parent if output_path is not None else None
        work_dir = tempfile.mkdtemp(prefix=".eegqc-task-", dir=parent)
        self.record = record
        self._work_dir = Path(work_dir)
        self._stop = Event()
        self._cancelled = self._context.Event()
        self._thread = Thread(
            target=self._transport,
            args=(record.task_id, payload, work_dir, self._stop, self._cancelled),
            daemon=True,
            name="eegqc-long-task",
        )
        self._thread.start()
        return record

    def _transport(self, task_id, payload, work_dir, stop, cancelled):
        reader, writer = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_run_task,
            args=(writer, cancelled, work_dir, payload, monotonic()),
            name="eegqc-scientific-task",
        )
        terminal = False
        result_started = None
        try:
            process.start()
            writer.close()
            while True:
                if stop.is_set():
                    cancelled.set()
                    process.join(0.15)  # give natural checkpoints a chance first
                    # Third-party ASR/ICA calls have no interrupt callback. Only
                    # this isolated child is terminated; it owns no GUI state.
                    if process.is_alive():
                        process.terminate()
                    break
                if reader.poll(0.05):
                    message = reader.recv()
                    if message[0] == "result_started":
                        result_started = message[1]
                        continue
                    if message[0] == "result" and result_started is not None:
                        self._messages.put(
                            (
                                task_id,
                                "ready",
                                {
                                    "result_transfer_seconds": monotonic() - result_started,
                                },
                            )
                        )
                    self._messages.put((task_id, *message))
                    if message[0] in ("result", "error", "cancelled"):
                        terminal = True
                        break
                elif not process.is_alive():
                    break
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join()
            if not terminal and not stop.is_set():
                self._messages.put((task_id, "error", f"Task process exited ({process.exitcode})"))
        except (EOFError, OSError, RuntimeError) as error:
            if not stop.is_set():
                self._messages.put((task_id, "error", f"Task process failed: {error}"))
        finally:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join()
            reader.close()
            writer.close()
            self._messages.put((task_id, "reaped", None))
            if stop.is_set():
                shutil.rmtree(work_dir, ignore_errors=True)

    def poll(self):
        while True:
            try:
                task_id, kind, value = self._messages.get_nowait()
            except Empty:
                break
            record = self.record
            if record is None or task_id != record.task_id:
                continue
            if kind == "reaped":
                if record.status != "running":
                    self._cleanup()
                continue
            if record.status != "running":
                continue  # a late result can never resurrect a cancelled task
            if kind == "ready":
                record.timings.update(value)
            elif kind == "stage":
                record.stage = value
            elif kind == "result":
                try:
                    if isinstance(value, StagedOutput):
                        source = value.source.resolve()
                        if source.parent != self._work_dir.resolve():
                            raise ValueError("Output does not belong to this task")
                        os.replace(source, value.destination)
                        value = value.destination
                    record.result = value
                    record.status = "succeeded"
                    record.stage = "完成"
                except (OSError, ValueError) as error:
                    record.error = str(error)
                    record.status = "failed"
                record.finished_at = monotonic()
                record.timings["total_seconds"] = record.elapsed
            elif kind in ("error", "cancelled"):
                record.status = "failed" if kind == "error" else "cancelled"
                record.error = value
                record.finished_at = monotonic()
        if self.record is not None and self.record.status != "running" and not self.busy:
            self._cleanup()
        return self.record

    def _cleanup(self):
        if self._work_dir is not None:
            shutil.rmtree(self._work_dir, ignore_errors=True)

    def cancel(self):
        if self.record is None or self.record.status != "running":
            return
        self.record.status = "cancelled"
        self.record.stage = "已取消"
        self.record.finished_at = monotonic()
        self.record.result = None
        self._cancelled.set()
        self._stop.set()

    def close(self):
        self.cancel()
        if self._thread is not None:
            self._thread.join(3)
        self.poll()
        self._cleanup()


def asr_task(context, snapshot, candidate):
    context.report("准备 ASR 输入")
    parent_signal = snapshot.engine.signal(candidate.asr.parent)
    context.check_cancelled()
    context.report("ASR 校准与重建")
    from eegqc.adapters.algorithms import asr_signal

    signal = asr_signal(snapshot.dataset, parent_signal, candidate.asr)
    context.report("传回 ASR 候选")
    return candidate, signal


def prepare_ica_task(context, snapshot, **kwargs):
    context.report("准备 ICA 拟合选择并计算有效秩")
    from eegqc.adapters.ica import prepare_ica

    result = prepare_ica(snapshot, **kwargs)
    context.report("传回 ICA 拟合范围与有效秩")
    return result


def fit_ica_task(context, prepared):
    context.report("ICA 拟合（EEGPrep Extended Infomax）")
    from eegqc.adapters.ica import fit_ica

    result = fit_ica(prepared)
    context.report("传回 ICA 候选分解")
    return result


def export_task(context, snapshot, path):
    from eegqc.adapters.eeglab import export_set

    path = Path(path).resolve()
    if snapshot.dataset.source is not None and path == snapshot.dataset.source.resolve():
        raise ValueError("Choose a different output file; the source dataset is preserved")
    output = context.output(path)
    context.report("重建已采用信号并写入临时导出")
    export_set(snapshot, output.source)
    context.report("准备发布完整导出")
    return output


def export_bundle_task(context, snapshot, path, **kwargs):
    from eegqc.epochlabel.bundle import export_bundle

    output = context.output(path)
    context.report("写入临时贡献包")
    export_bundle(snapshot, output.source, **kwargs)
    context.report("准备发布完整贡献包")
    return output
