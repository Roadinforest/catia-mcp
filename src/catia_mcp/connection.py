"""Run synchronous CATIA callbacks in a Windows COM apartment."""

import asyncio
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

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
    import win32com.client
    import winerror

    # All clients share the interactive CATIA session. Serialize whole operations.
    with _access:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            try:
                application = win32com.client.GetActiveObject("CATIA.Application")
            except pythoncom.com_error as exc:
                if exc.hresult != winerror.MK_E_UNAVAILABLE:
                    raise
                application = win32com.client.Dispatch("CATIA.Application")

            try:
                return operation(application)
            finally:
                del application
        finally:
            pythoncom.CoUninitialize()
