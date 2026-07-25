# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""AutoCADPluginLoader -- the COM delivery worker that NETLOADs the bundled plugin.

The COM nav *transport* is archived (archive/autocad_com_transport/); the loader's whole
contract is: attach to a running AutoCAD, and once a document is open copy the bundled
DLL + version.json to the runtime dir, extend TRUSTEDPATHS, and send ONE NETLOAD per
AutoCAD session (a new session after a drop gets its own). These tests drive _tick()
directly against stub COM objects -- no AutoCAD, no pywin32 COM calls.
"""
from types import SimpleNamespace

import pytest

from trackball_daemon import autocad_driver, integrations
from trackball_daemon.autocad_driver import AutoCADPluginLoader


class FakeDoc:
    def __init__(self, trusted=""):
        self.trusted = trusted
        self.sets = []
        self.commands = []

    def GetVariable(self, name):
        assert name == "TRUSTEDPATHS"
        return self.trusted

    def SetVariable(self, name, value):
        self.sets.append((name, value))
        self.trusted = value

    def SendCommand(self, cmd):
        self.commands.append(cmd)


class FakeAcad:
    def __init__(self, doc, doc_count=1):
        self.doc = doc
        self.doc_count = doc_count
        self.alive = True

    @property
    def Documents(self):
        if not self.alive:
            raise OSError("COM handle dropped (app closed)")
        return SimpleNamespace(Count=self.doc_count)

    @property
    def ActiveDocument(self):
        return self.doc


@pytest.fixture
def plugin_dirs(tmp_path, monkeypatch):
    """A real bundled DLL + version.json in a temp 'bundled' dir, and a temp runtime dir."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / autocad_driver._PLUGIN_DLL).write_bytes(b"MZ fake plugin")
    (bundled / "version.json").write_text('{"version": "9.9.9"}', encoding="utf-8")
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(autocad_driver, "_bundled_plugin_path",
                        lambda: bundled / autocad_driver._PLUGIN_DLL)
    monkeypatch.setattr(autocad_driver, "_runtime_plugin_dir", lambda: runtime)
    return bundled, runtime


def _attached_loader(monkeypatch, acad):
    monkeypatch.setattr(AutoCADPluginLoader, "_find_running_acad", staticmethod(lambda: acad))
    loader = AutoCADPluginLoader()
    loader.set_enabled(True)
    return loader


def test_disabled_loader_does_not_enumerate_or_mutate(plugin_dirs, monkeypatch):
    doc = FakeDoc()
    calls = []
    monkeypatch.setattr(AutoCADPluginLoader, "_find_running_acad",
                        staticmethod(lambda: calls.append(True) or FakeAcad(doc)))
    loader = AutoCADPluginLoader()                       # disabled until successful setup
    loader._tick()
    assert calls == []
    assert doc.sets == [] and doc.commands == []


def test_attach_copies_and_netloads_once(plugin_dirs, monkeypatch):
    bundled, runtime = plugin_dirs
    doc = FakeDoc()
    ldr = _attached_loader(monkeypatch, FakeAcad(doc))
    ldr._tick()                                              # attach + NETLOAD in one poll
    assert (runtime / autocad_driver._PLUGIN_DLL).read_bytes() == b"MZ fake plugin"
    assert (runtime / "version.json").read_text(encoding="utf-8") == '{"version": "9.9.9"}'
    assert str(runtime) in doc.trusted                       # TRUSTEDPATHS extended
    assert len(doc.commands) == 1 and "NETLOAD" in doc.commands[0]
    assert "\\" not in doc.commands[0].split('"')[3]         # path sent with forward slashes
    ldr._tick()                                              # same session -> no second NETLOAD
    ldr._tick()
    assert len(doc.commands) == 1


def test_waits_for_a_document(plugin_dirs, monkeypatch):
    # App alive on the Start tab (no drawing): SendCommand has nowhere to go -> wait, then load.
    doc = FakeDoc()
    acad = FakeAcad(doc, doc_count=0)
    ldr = _attached_loader(monkeypatch, acad)
    ldr._tick()
    assert doc.commands == [] and not ldr._netload_done
    acad.doc_count = 1
    ldr._tick()
    assert len(doc.commands) == 1


def test_new_session_gets_its_own_netload(plugin_dirs, monkeypatch):
    doc = FakeDoc()
    ldr = _attached_loader(monkeypatch, FakeAcad(doc))
    ldr._tick()
    assert len(doc.commands) == 1
    # Session ends: _run's exception handler clears the handle; a NEW acad then attaches.
    doc2 = FakeDoc()
    monkeypatch.setattr(AutoCADPluginLoader, "_find_running_acad",
                        staticmethod(lambda: FakeAcad(doc2)))
    ldr._acad = None
    ldr._tick()
    assert len(doc2.commands) == 1                           # fresh session -> fresh NETLOAD


def test_liveness_probe_raises_when_app_gone(plugin_dirs, monkeypatch):
    # After the NETLOAD the tick is a pure liveness probe; a dead handle must RAISE so _run
    # drops it and re-attaches later (that reset is what test_new_session... exercises).
    acad = FakeAcad(FakeDoc())
    ldr = _attached_loader(monkeypatch, acad)
    ldr._tick()
    acad.alive = False
    with pytest.raises(Exception):
        ldr._tick()


def test_locked_dll_still_netloads_existing_copy(plugin_dirs, monkeypatch):
    # AutoCAD holds the runtime DLL locked (already loaded once this session, or a previous
    # session's daemon copied it): the copy fails but the NETLOAD of the existing file proceeds,
    # and the version manifest is NOT bumped past the DLL that actually loads.
    bundled, runtime = plugin_dirs
    runtime.mkdir()
    (runtime / autocad_driver._PLUGIN_DLL).write_bytes(b"old locked dll")
    (runtime / "version.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    real_copy2 = integrations.shutil.copy2

    def deny_dll(src, dst, **kw):
        if str(src).endswith(".dll"):
            raise OSError("locked")
        return real_copy2(src, dst, **kw)

    monkeypatch.setattr(integrations.shutil, "copy2", deny_dll)
    doc = FakeDoc()
    _attached_loader(monkeypatch, FakeAcad(doc))._tick()
    assert len(doc.commands) == 1 and "NETLOAD" in doc.commands[0]
    assert (runtime / "version.json").read_text(encoding="utf-8") == '{"version": "1.0.0"}'


def test_trustedpaths_not_duplicated(plugin_dirs, monkeypatch):
    bundled, runtime = plugin_dirs
    doc = FakeDoc(trusted=str(runtime).upper())              # already trusted (case-insensitive)
    _attached_loader(monkeypatch, FakeAcad(doc))._tick()
    assert doc.sets == []
    assert len(doc.commands) == 1


def test_trustedpaths_requires_an_exact_entry(plugin_dirs, monkeypatch):
    _bundled, runtime = plugin_dirs
    doc = FakeDoc(trusted=str(runtime.parent))               # parent must not count as exact trust
    _attached_loader(monkeypatch, FakeAcad(doc))._tick()
    assert doc.sets and doc.trusted.split(";")[-1] == str(runtime)


def test_missing_bundled_plugin_is_a_noop(plugin_dirs, monkeypatch):
    bundled, runtime = plugin_dirs
    (bundled / autocad_driver._PLUGIN_DLL).unlink()
    doc = FakeDoc()
    ldr = _attached_loader(monkeypatch, FakeAcad(doc))
    ldr._tick()
    assert doc.commands == [] and doc.sets == []
    assert ldr._netload_done                                 # doesn't retry-spam every poll


def test_start_without_pywin32_is_a_noop(monkeypatch):
    monkeypatch.setattr(autocad_driver, "_PYWIN32", False)
    ldr = AutoCADPluginLoader()
    ldr.start()
    assert ldr._thread is None
