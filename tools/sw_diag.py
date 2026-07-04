"""SolidWorks COM view-API diagnostic — run this to get GROUND TRUTH for the trackball driver.

The trackball SolidWorks integration drives the view over out-of-process COM, and a few of
those APIs don't behave the way the docs imply (orbit axes, pan, redraw). This script attaches
to a RUNNING SolidWorks and exercises each operation in isolation, printing what it sees and
pausing so you can watch the viewport. Paste the full output back and I'll fix the driver from
real data instead of guessing.

HOW TO RUN
  1. Open SolidWorks with a part or assembly.
  2. Rotate the view to a clearly tilted ISOMETRIC angle (NOT a flat Front view) and Zoom to Fit,
     so rotations/pans are easy to judge.
  3. In a terminal (from the repo root):   python tools/sw_diag.py
  4. Follow the prompts; after each step say (out loud / in notes) what the viewport did.

It restores each change right after, so your view is left roughly where it started.
"""
import sys
import time

try:
    import pythoncom  # noqa: F401  (imported for completeness / COM init parity)
    import win32com.client
except Exception as exc:  # pragma: no cover
    print("pywin32 is not installed:", exc)
    sys.exit(1)


def pause(msg):
    try:
        input("\n>>> " + msg + "  [press Enter to continue] ")
    except EOFError:
        pass


def fmt(seq):
    return "[" + ", ".join(f"{v:+.4f}" for v in seq) + "]"


def main():
    try:
        app = win32com.client.GetActiveObject("SldWorks.Application")
    except Exception as exc:
        print("Could not attach to a running SolidWorks (is it open?):", repr(exc))
        return
    print("Attached. RevisionNumber:", end=" ")
    try:
        print(app.RevisionNumber)
    except Exception as exc:
        print("(failed:", repr(exc), ")")

    model = app.ActiveDoc
    if model is None:
        print("No active document — open a part/assembly first.")
        return
    try:
        print("Active doc:", model.GetTitle())
    except Exception:
        pass
    view = model.ActiveView
    if view is None:
        print("No active view.")
        return

    # --- 1. Orientation3.ArrayData: the heart of the 'orbit is global' mystery -------------
    print("\n=== 1. Orientation3.ArrayData ===")
    try:
        ad = tuple(view.Orientation3.ArrayData)
        print("len:", len(ad))
        print("rot row0 (x):", fmt(ad[0:3]))
        print("rot row1 (y):", fmt(ad[3:6]))
        print("rot row2 (z):", fmt(ad[6:9]))
        print("translation :", fmt(ad[9:12]))
        print("scale       :", f"{ad[12]:+.4f}")
        print(">>> If this looks like the identity (row0=[+1,0,0], row1=[0,+1,0], row2=[0,0,+1])")
        print(">>> even though the view is clearly tilted, that's our 'orbit is global' bug.")
    except Exception as exc:
        ad = None
        print("Orientation3.ArrayData FAILED:", repr(exc))

    # --- 2. RotateAboutAxis: is the axis honored, and is it camera- or model-relative? -----
    # Rotate ~25 deg about the model X axis, then about ArrayData row0, then undo each.
    deg = 0.4363  # ~25 degrees in radians
    print("\n=== 2. RotateAboutAxis (does it honor the axis? camera vs global?) ===")
    _try_rotate(model, view, deg, (1.0, 0.0, 0.0), "MODEL X axis (1,0,0)")
    pause("Did the view rotate about the GLOBAL part X axis just now?")
    _try_rotate(model, view, -deg, (1.0, 0.0, 0.0), "undo MODEL X")

    if ad is not None:
        row0 = (ad[0], ad[1], ad[2])
        col0 = (ad[0], ad[3], ad[6])
        _try_rotate(model, view, deg, row0, "ArrayData ROW0 " + fmt(row0))
        pause("Did THAT rotate about the SCREEN horizontal (pitch, like dragging top toward you)?")
        _try_rotate(model, view, -deg, row0, "undo ROW0")
        _try_rotate(model, view, deg, col0, "ArrayData COL0 " + fmt(col0))
        pause("Did THAT one rotate about the SCREEN horizontal instead?")
        _try_rotate(model, view, -deg, col0, "undo COL0")

    # --- 3. Does RotateAboutAxis redraw on its own (no GraphicsRedraw2)? -------------------
    print("\n=== 3. Auto-redraw check (RotateAboutAxis WITHOUT a forced redraw) ===")
    try:
        view.RotateAboutAxis(deg, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)  # about screen-ish vertical
        print("Called RotateAboutAxis but did NOT call GraphicsRedraw2.")
        pause("Did the viewport visibly rotate WITHOUT a forced redraw?")
        view.RotateAboutAxis(-deg, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        try:
            model.GraphicsRedraw2()
        except Exception:
            pass
    except Exception as exc:
        print("RotateAboutAxis FAILED:", repr(exc))

    # --- 4. Pan via TranslateBy: does it move the view, and does it throw? -----------------
    print("\n=== 4. Pan (TranslateBy) ===")
    math_util = None
    try:
        math_util = app.GetMathUtility()
    except Exception as exc:
        print("GetMathUtility FAILED:", repr(exc))
    if math_util is not None:
        for mag in (0.01, 0.1, 0.5):
            try:
                vec = math_util.CreateVector((mag, 0.0, 0.0))
                view.TranslateBy(vec)
                try:
                    model.GraphicsRedraw2()
                except Exception:
                    pass
                print(f"TranslateBy(({mag},0,0)) OK")
                pause(f"Did the view PAN sideways by a visible amount at magnitude {mag}?")
                back = math_util.CreateVector((-mag, 0.0, 0.0))
                view.TranslateBy(back)
                try:
                    model.GraphicsRedraw2()
                except Exception:
                    pass
            except Exception as exc:
                print(f"TranslateBy at magnitude {mag} FAILED:", repr(exc))

    # --- 5. Zoom via ZoomByFactor: does it zoom about the center? --------------------------
    print("\n=== 5. Zoom (ZoomByFactor) ===")
    try:
        view.ZoomByFactor(1.25)
        try:
            model.GraphicsRedraw2()
        except Exception:
            pass
        pause("Did it zoom IN about the CENTER of the view (not drifting off-screen)?")
        view.ZoomByFactor(1.0 / 1.25)
        try:
            model.GraphicsRedraw2()
        except Exception:
            pass
    except Exception as exc:
        print("ZoomByFactor FAILED:", repr(exc))

    print("\nDone. Please paste everything above (and your spoken answers to each prompt).")


def _try_rotate(model, view, angle, axis, label):
    try:
        view.RotateAboutAxis(angle, 0.0, 0.0, 0.0, axis[0], axis[1], axis[2])
        try:
            model.GraphicsRedraw2()
        except Exception:
            pass
        print(f"RotateAboutAxis about {label}: OK")
    except Exception as exc:
        print(f"RotateAboutAxis about {label}: FAILED {exc!r}")
    time.sleep(0.05)


if __name__ == "__main__":
    main()
