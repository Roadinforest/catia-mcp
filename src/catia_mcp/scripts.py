"""Execute a Python script against the worker's CATIA instance in a child process."""

import runpy
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile


OUTPUT_BYTES = 64 * 1024


def read_output(file):
    size = file.seek(0, 2)
    file.seek(0)
    if size <= OUTPUT_BYTES:
        return file.read().decode("utf-8", errors="replace")
    head = file.read(OUTPUT_BYTES // 2)
    file.seek(-OUTPUT_BYTES // 2, 2)
    tail = file.read()
    return (head + b"\n... [output truncated] ...\n" + tail).decode("utf-8", errors="replace")


def execute_script(session, code, timeout_seconds, model_id=None):
    import pythoncom

    if model_id is not None:
        session.get(model_id).doc.Activate()

    with TemporaryDirectory(prefix="catia-script-") as directory, TemporaryFile() as out, TemporaryFile() as err:
        script = Path(directory) / "catia_script.py"
        script.write_text(code, encoding="utf-8")
        # Marshal the actual application, even when Windows has several CATIA instances.
        stream = pythoncom.CreateStreamOnHGlobal()
        pythoncom.CoMarshalInterface(
            stream, pythoncom.IID_IDispatch, session.app._oleobj_,
            pythoncom.MSHCTX_LOCAL, pythoncom.MSHLFLAGS_TABLESTRONG,
        )
        try:
            size = stream.Seek(0, 1)
            stream.Seek(0, 0)
            command = [sys.executable, "-X", "utf8", "-u", "-m", "catia_mcp.scripts", str(script)]
            try:
                proc = subprocess.run(
                    command, input=stream.Read(size), stdout=out, stderr=err,
                    timeout=timeout_seconds, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                exit_code = None
        finally:
            # A script can modify any bound part, including before an exception or timeout.
            for model in session.models.values():
                model.targets.changed()
            stream.Seek(0, 0)
            pythoncom.CoReleaseMarshalData(stream)

        return {
            "exit_code": exit_code,
            "timed_out": exit_code is None,
            "stdout": read_output(out),
            "stderr": read_output(err),
        }


if __name__ == "__main__":
    import pythoncom
    import win32com.client

    stream = pythoncom.CreateStreamOnHGlobal()
    stream.Write(sys.stdin.buffer.read())
    stream.Seek(0, 0)
    dispatch = pythoncom.CoGetInterfaceAndReleaseStream(stream, pythoncom.IID_IDispatch)
    catia = win32com.client.Dispatch(dispatch)
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], init_globals={"catia": catia}, run_name="__main__")
