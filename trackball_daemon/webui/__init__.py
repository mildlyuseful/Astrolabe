# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""The settings UI: a static web page served on loopback and shown in a browser app window."""

from .server import UiServer
from .window import open_window

__all__ = ("UiServer", "open_window")
