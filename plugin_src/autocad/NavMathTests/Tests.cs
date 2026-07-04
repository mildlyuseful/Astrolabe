// Offline unit tests for NavMath.Apply's "pointer" pivot path (orbitPivot / zoomPivot), feeding a
// SYNTHETIC pivot point -- the pure-math half of what TBNAVPTRTEST verifies in-process. Exit 0 on
// pass, 1 on failure (run by tests/test_acad_navmath_pointer.py).
using System;
using Autodesk.AutoCAD.Geometry;
using TrackballNav;

static class Tests
{
    static int s_failures;
    static readonly double[] Sign = { 1.0, 1.0, 1.0 };
    const double PanSignX = -1.0, PanSignY = 1.0, PanScale = 0.5;
    const double ZoomSign = 1.0, ZoomScale = 0.5;

    static void Check(bool ok, string what)
    {
        Console.WriteLine($"{(ok ? "PASS" : "FAIL")}  {what}");
        if (!ok) s_failures++;
    }

    static void CheckClose(double a, double b, double tol, string what) =>
        Check(Math.Abs(a - b) <= tol, $"{what}  ({a:G10} vs {b:G10})");

    // A camera 25 units from an off-origin target, oblique view, properly orthogonal up.
    static CamState Cam(bool persp = false)
    {
        var tgt = new Point3d(4.0, -3.0, 2.0);
        var dir = new Vector3d(0.5, -0.7, 0.4).GetNormal();      // target -> camera
        var lvlRight = NavMath.WorldUp.CrossProduct(dir).GetNormal();
        var up = dir.CrossProduct(lvlRight).GetNormal();
        return new CamState
        {
            Pos = tgt + dir * 25.0,
            Tgt = tgt,
            Up = up,
            Fw = 16.0,
            Fh = 9.0,
            Persp = persp,
        };
    }

    static (Vector3d right, Vector3d up) Basis(CamState c)
    {
        var d = (c.Pos - c.Tgt).GetNormal();
        var up = c.Up.GetNormal();
        var right = up.CrossProduct(d).GetNormal();
        return (right, up);
    }

    static double[] D(double ox = 0, double oy = 0, double oz = 0,
                      double px = 0, double py = 0, double z = 0) =>
        new[] { ox, oy, oz, px, py, z };

    static CamState Apply(CamState c, double[] d, string style,
                          Point3d? orbitPivot = null, Point3d? zoomPivot = null) =>
        NavMath.Apply(c, d, style, Sign, PanSignX, PanSignY, PanScale, ZoomSign, ZoomScale,
                      orbitPivot, zoomPivot);

    static void OrbitAboutPivotRigid(string style)
    {
        var c0 = Cam();
        var P = new Point3d(9.0, 1.0, 4.0);                       // synthetic "cursor point"
        var d = style == "turntable" ? D(0.20, 0.35) : D(0.20, 0.35, 0.10);
        var c1 = Apply(c0, d, style, orbitPivot: P);

        double tol = 1e-9 * 30.0;
        CheckClose((c1.Tgt - P).Length, (c0.Tgt - P).Length, tol, $"{style}: |tgt-P| preserved");
        CheckClose((c1.Pos - P).Length, (c0.Pos - P).Length, tol, $"{style}: |pos-P| preserved");
        CheckClose((c1.Pos - c1.Tgt).Length, (c0.Pos - c0.Tgt).Length, tol,
                   $"{style}: view distance preserved");
        Check((c1.Tgt - c0.Tgt).Length > 1e-3, $"{style}: target actually swept around the pivot");

        // THE user-visible invariant: P keeps its exact screen position (same right/up offsets
        // from the optical axis before and after).
        var b0 = Basis(c0);
        var b1 = Basis(c1);
        CheckClose((P - c1.Tgt).DotProduct(b1.right), (P - c0.Tgt).DotProduct(b0.right), tol,
                   $"{style}: P screen-x fixed");
        CheckClose((P - c1.Tgt).DotProduct(b1.up), (P - c0.Tgt).DotProduct(b0.up), tol,
                   $"{style}: P screen-y fixed");

        if (style == "turntable")
        {
            var right1 = Basis(c1).right;
            CheckClose(right1.DotProduct(NavMath.WorldUp), 0.0, 1e-9,
                       "turntable: horizon stays level with a pivot");
        }
    }

    static void Main()
    {
        // --- null pivot == the pre-0.3.0 behaviour: the target never moves on orbit ----------
        {
            var c0 = Cam();
            var c1 = Apply(c0, D(0.20, 0.35, 0.10), "free");
            CheckClose((c1.Tgt - c0.Tgt).Length, 0.0, 1e-12, "no pivot: target fixed");
            CheckClose((c1.Pos - c1.Tgt).Length, 25.0, 1e-9, "no pivot: distance preserved");
        }

        // --- pivot == target reduces exactly to the null-pivot result ------------------------
        {
            var c0 = Cam();
            var a = Apply(c0, D(0.20, 0.35, 0.10), "free");
            var b = Apply(c0, D(0.20, 0.35, 0.10), "free", orbitPivot: c0.Tgt);
            CheckClose((a.Pos - b.Pos).Length, 0.0, 1e-9, "pivot==target: same eye");
            CheckClose((a.Tgt - b.Tgt).Length, 0.0, 1e-9, "pivot==target: same target");
        }

        OrbitAboutPivotRigid("free");
        OrbitAboutPivotRigid("turntable");

        // --- to_pointer zoom (parallel): P's screen position is fixed while the field shrinks --
        {
            var c0 = Cam();
            var P = new Point3d(9.0, 1.0, 4.0);
            var c1 = Apply(c0, D(z: 0.8), "free", zoomPivot: P);
            double factor = 1.0 + ZoomSign * 0.8 * ZoomScale;
            CheckClose(c1.Fh, c0.Fh / factor, 1e-12, "to_pointer zoom: field shrinks by 1/factor");
            var b0 = Basis(c0);
            var b1 = Basis(c1);
            CheckClose((P - c1.Tgt).DotProduct(b1.right) / c1.Fh,
                       (P - c0.Tgt).DotProduct(b0.right) / c0.Fh, 1e-12,
                       "to_pointer zoom: P screen-x fraction fixed");
            CheckClose((P - c1.Tgt).DotProduct(b1.up) / c1.Fh,
                       (P - c0.Tgt).DotProduct(b0.up) / c0.Fh, 1e-12,
                       "to_pointer zoom: P screen-y fraction fixed");
        }

        // --- zoom without a pivot: the target must not move (to_center behaviour) -------------
        {
            var c0 = Cam();
            var c1 = Apply(c0, D(z: 0.8), "free");
            CheckClose((c1.Tgt - c0.Tgt).Length, 0.0, 1e-12, "plain zoom: target fixed");
        }

        // --- perspective + zoomPivot: falls back to the plain dolly (documented limitation) ---
        {
            var c0 = Cam(persp: true);
            var P = new Point3d(9.0, 1.0, 4.0);
            var c1 = Apply(c0, D(z: 0.8), "free", zoomPivot: P);
            CheckClose((c1.Tgt - c0.Tgt).Length, 0.0, 1e-12, "persp to_pointer: target fixed (dolly)");
            Check((c1.Pos - c1.Tgt).Length < 25.0, "persp to_pointer: dollied in");
        }

        // --- pan is unaffected by an orbit pivot (independent channels) -----------------------
        {
            var c0 = Cam();
            var P = new Point3d(9.0, 1.0, 4.0);
            var a = Apply(c0, D(px: 0.3, py: -0.2), "free");
            var b = Apply(c0, D(px: 0.3, py: -0.2), "free", orbitPivot: P);
            CheckClose((a.Tgt - b.Tgt).Length, 0.0, 1e-12, "pan: identical with/without orbit pivot");
        }

        Console.WriteLine(s_failures == 0 ? "ALL PASS" : $"{s_failures} FAILURE(S)");
        Environment.Exit(s_failures == 0 ? 0 : 1);
    }
}
