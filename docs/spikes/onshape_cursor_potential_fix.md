# POTENTIAL FIX — Onshape cursor mapping (NOT CURRENTLY APPLIED)
# Saved 2026-07-07 before reverting due to daemon launch issue.
#
# The original _cursor_client_fraction() used WindowFromPoint() which has two bugs:
#
# 1. Firefox: WindowFromPoint returns the TOP-LEVEL MozillaWindowClass window whose
#    GetClientRect INCLUDES browser chrome (tabs + address bar, ~75-97px). This makes
#    h/w wrong for the auto-left formula → horizontal scaling error, and fy includes
#    browser chrome offset → vertical error.
#
# 2. Chrome: creates multiple Chrome_RenderWidgetHostHWND child windows. The first one
#    found may be a tiny UI widget (e.g. 1679×121px) instead of the main viewport.
#
# Fix approach (3-tier detection in _get_browser_content_rect):
#   Tier 1: Find LARGEST Chrome_RenderWidgetHostHWND, validate area > 50000px² + sane aspect
#   Tier 2: Firefox/Chrome fallback — main window client rect minus DPI-scaled browser chrome (78px @ 96dpi)
#   Tier 3: Unknown browser — raw client rect
#
# _find_descendant_by_class was changed to return the LARGEST matching window by area
# (was: first match).
#
# Live-test results with Chrome (1920×1032 viewport): horizontal NDC error = 0.000
# All 19 unit tests pass.
#
# For the full diff, see the original commit or the git reflog.
