"""Run synchronous CATIA callbacks in a Windows COM apartment."""

import asyncio
import os
from pathlib import Path
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, TypeVar

from .profiles import Profiles

if TYPE_CHECKING:
    from win32com.client import CDispatch

T = TypeVar("T")
_access = threading.Lock()


async def run_in_catia(operation: Callable[["CDispatch"], T]) -> T:
    """Run one operation and return plain data. COM objects stay on the calling worker."""
    return await asyncio.to_thread(_run, operation)


def _run(operation: Callable[["CDispatch"], T]) -> T:
    if sys.platform != "win32":
        raise RuntimeError("CATIA automation requires Windows and CATIA V5.")

    import pythoncom

    # All clients share the interactive CATIA session. Serialize whole operations.
    with _access:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            application = _application()

            try:
                return operation(application)
            finally:
                del application
        finally:
            pythoncom.CoUninitialize()


def _application():
    import pythoncom
    import win32com.client
    import winerror

    expected = os.environ.get("CATIA_MCP_EXPECTED_SETTINGS")
    if expected:
        # Explicit test sessions must never launch or fall back to a user session.
        application = win32com.client.GetActiveObject("CATIA.Application")
        actual = application.SystemService.Environ("CATUserSettingPath")
        if Path(actual).resolve() != Path(expected).resolve():
            raise RuntimeError("CATIA settings identity mismatch; refusing the connection")
        return application

    try:
        return win32com.client.GetActiveObject("CATIA.Application")
    except pythoncom.com_error as exc:
        if exc.hresult != winerror.MK_E_UNAVAILABLE:
            raise
        return win32com.client.Dispatch("CATIA.Application")


class CadWorker:
    """Keep model bindings in one COM apartment for the server's lifetime."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catia")
        self.session = None
        self.profiles = Profiles()

    def _call(self, operation):
        if sys.platform != "win32":
            raise RuntimeError("CATIA automation requires Windows and CATIA V5.")
        if self.session is None:
            import pythoncom

            from .modeling import Session

            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            try:
                self.session = Session(_application())
            except BaseException:
                pythoncom.CoUninitialize()
                raise
        with _access:
            return operation(self.session)

    async def call(self, operation):
        return await asyncio.get_running_loop().run_in_executor(self.pool, self._call, operation)

    def _release(self):
        if self.session is not None:
            import pythoncom

            self.session = None
            pythoncom.CoUninitialize()

    async def close(self):
        try:
            await asyncio.get_running_loop().run_in_executor(self.pool, self._release)
        finally:
            self.pool.shutdown()
