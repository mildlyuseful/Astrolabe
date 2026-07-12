// Offline unit tests for NavMath.Apply's "cursor" pivot path (orbitPivot / zoomPivot), feeding a
// SYNTHETIC pivot point -- the pure-math half of what TBNAVPTRTEST verifies in-process. Exit 0 on
// pass, 1 on failure (run by tests/test_autocad_navmath_cursor.py).
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
                          Point3d? orbitPivot = null, Point3d? zoomPivot = null,
                          bool levelOnEntry = false) =>
        NavMath.Apply(c, d, style, Sign, PanSignX, PanSignY, PanScale, ZoomSign, ZoomScale,
                      orbitPivot, zoomPivot, levelOnEntry);

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
        // --- level-horizon is a one-shot transition operation, not an ordinary frame rule ------
        {
            var rolled = Cam();
            var back = (rolled.Pos - rolled.Tgt).GetNormal();
            rolled.Up = rolled.Up.TransformBy(
                Matrix3d.Rotation(0.65, back, Point3d.Origin)).GetNormal();
            var leveled = Apply(rolled, D(), "turntable", levelOnEntry: true);
            CheckClose((leveled.Pos - rolled.Pos).Length, 0.0, 1e-12,
                       "level entry: eye preserved");
            CheckClose((leveled.Tgt - rolled.Tgt).Length, 0.0, 1e-12,
                       "level entry: target preserved");
            CheckClose(Basis(leveled).right.DotProduct(NavMath.WorldUp), 0.0, 1e-9,
                       "level entry: horizon horizontal");
            Check(leveled.Up.DotProduct(NavMath.WorldUp) > 0.0,
                  "level entry: up remains world-up-positive");
            var twice = Apply(leveled, D(), "turntable", levelOnEntry: true);
            CheckClose((twice.Up - leveled.Up).Length, 0.0, 1e-12,
                       "level entry: idempotent");
            var preserved = Apply(rolled, D(), "turntable", levelOnEntry: false);
            CheckClose((preserved.Up - rolled.Up).Length, 0.0, 1e-12,
                       "level toggle off: current tilt preserved");
        }

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

        // --- to_cursor zoom (parallel): P's screen position is fixed while the field shrinks --
        {
            var c0 = Cam();
            var P = new Point3d(9.0, 1.0, 4.0);
            var c1 = Apply(c0, D(z: 0.8), "free", zoomPivot: P);
            double factor = 1.0 + ZoomSign * 0.8 * ZoomScale;
            CheckClose(c1.Fh, c0.Fh / factor, 1e-12, "to_cursor zoom: field shrinks by 1/factor");
            var b0 = Basis(c0);
            var b1 = Basis(c1);
            CheckClose((P - c1.Tgt).DotProduct(b1.right) / c1.Fh,
                       (P - c0.Tgt).DotProduct(b0.right) / c0.Fh, 1e-12,
                       "to_cursor zoom: P screen-x fraction fixed");
            CheckClose((P - c1.Tgt).DotProduct(b1.up) / c1.Fh,
                       (P - c0.Tgt).DotProduct(b0.up) / c0.Fh, 1e-12,
                       "to_cursor zoom: P screen-y fraction fixed");
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
            CheckClose((c1.Tgt - c0.Tgt).Length, 0.0, 1e-12, "persp to_cursor: target fixed (dolly)");
            Check((c1.Pos - c1.Tgt).Length < 25.0, "persp to_cursor: dollied in");
        }

        // --- pan is unaffected by an orbit pivot (independent channels) -----------------------
        {
            var c0 = Cam();
            var P = new Point3d(9.0, 1.0, 4.0);
            var a = Apply(c0, D(px: 0.3, py: -0.2), "free");
            var b = Apply(c0, D(px: 0.3, py: -0.2), "free", orbitPivot: P);
            CheckClose((a.Tgt - b.Tgt).Length, 0.0, 1e-12, "pan: identical with/without orbit pivot");
        }

        // --- AtViewDepth: slide along VIEWDIR to the plane through the look-at ---------------
        {
            // Oblique view: UCS-plane (z=0) point under the cursor is far from the scene; sliding
            // to the target depth keeps screen XY and lands inside a typical extents box.
            var onPlane = new Point3d(100.0, 50.0, 0.0);
            var vd = new Vector3d(1.0, 1.0, 1.0).GetNormal();   // VIEWDIR: target -> camera
            var tgt = new Point3d(10.0, 10.0, 10.0);
            var p = NavMath.AtViewDepth(onPlane, vd, tgt);
            CheckClose((p - tgt).DotProduct(vd), 0.0, 1e-9, "AtViewDepth: result on target-depth plane");
            CheckClose((p - onPlane).CrossProduct(vd).Length, 0.0, 1e-9,
                       "AtViewDepth: result stays on the view ray");
            // Parallel: a point already at target depth is unchanged
            var onDepth = NavMath.AtViewDepth(tgt + new Vector3d(3, -2, 0),
                                              Vector3d.ZAxis, tgt);
            CheckClose(onDepth.Z, tgt.Z, 1e-12, "AtViewDepth: top view preserves Z=target");
        }

        // --- InsideGrownExtents ---------------------------------------------------------------
        {
            var mn = new Point3d(0, 0, 0);
            var mx = new Point3d(10, 10, 10);
            Check(NavMath.InsideGrownExtents(new Point3d(5, 5, 5), mn, mx), "extents: centre in");
            Check(NavMath.InsideGrownExtents(new Point3d(-1, 5, 5), mn, mx), "extents: 10% margin in");
            Check(!NavMath.InsideGrownExtents(new Point3d(-50, 5, 5), mn, mx), "extents: far OOB");
            // unset extents (EXTMAX < EXTMIN) -> accept
            Check(NavMath.InsideGrownExtents(new Point3d(1e6, 0, 0),
                      new Point3d(1, 1, 1), new Point3d(0, 0, 0)), "extents: unset accepts");
        }

        // --- AabbNearHit: near face of a solid, not the centre / back face --------------------
        {
            var mn = new Point3d(0, 0, 0);
            var mx = new Point3d(20, 20, 20);
            // Looking down -Z (VIEWDIR = +Z): ray through (10,10,100) into the scene hits z=20
            var hit = NavMath.AabbNearHit(new Point3d(10, 10, 100), Vector3d.ZAxis, mn, mx);
            Check(hit.HasValue, "AabbNearHit: top-down hit");
            CheckClose(hit.Value.Z, 20.0, 1e-9, "AabbNearHit: near face z=20 (not centre 10)");
            CheckClose(hit.Value.X, 10.0, 1e-9, "AabbNearHit: under cursor X");
            // Looking along +X (VIEWDIR = +X, into scene = -X): near face is x=20
            var hitX = NavMath.AabbNearHit(new Point3d(100, 10, 10), Vector3d.XAxis, mn, mx);
            Check(hitX.HasValue, "AabbNearHit: side hit");
            CheckClose(hitX.Value.X, 20.0, 1e-9, "AabbNearHit: near face x=20 (not back x=0)");
            // Miss
            Check(!NavMath.AabbNearHit(new Point3d(50, 50, 100), Vector3d.ZAxis, mn, mx).HasValue,
                  "AabbNearHit: miss returns null");
            // onPlane INSIDE the box (UCS plane cutting through a solid): still the near face,
            // never the interior sample (that produced pivots like (4.2,6.1,6.4) on a [0,10]^3).
            var inside = NavMath.AabbNearHit(new Point3d(10, 10, 10), Vector3d.ZAxis, mn, mx);
            Check(inside.HasValue, "AabbNearHit: inside hit");
            CheckClose(inside.Value.Z, 20.0, 1e-9, "AabbNearHit: inside -> near face z=20");
            CheckClose(inside.Value.X, 10.0, 1e-9, "AabbNearHit: inside stays under cursor X");
            CheckClose(inside.Value.Y, 10.0, 1e-9, "AabbNearHit: inside stays under cursor Y");
            // Cube [0,10]^3, oblique VIEWDIR, UCS-plane sample inside: one coord must be 0 or 10
            {
                var cMn = new Point3d(0, 0, 0);
                var cMx = new Point3d(10, 10, 10);
                var vd = new Vector3d(1, 1, 1).GetNormal();
                var mid = NavMath.AabbNearHit(new Point3d(5, 5, 5), vd, cMn, cMx);
                Check(mid.HasValue, "AabbNearHit: cube interior sample hits");
                bool onFace =
                    Math.Abs(mid.Value.X) < 1e-9 || Math.Abs(mid.Value.X - 10) < 1e-9 ||
                    Math.Abs(mid.Value.Y) < 1e-9 || Math.Abs(mid.Value.Y - 10) < 1e-9 ||
                    Math.Abs(mid.Value.Z) < 1e-9 || Math.Abs(mid.Value.Z - 10) < 1e-9;
                Check(onFace, "AabbNearHit: cube interior sample lands on a face (0 or 10)");
            }
        }

        // --- AabbNearHitThick: expanding aperture recovers a near miss --------------------
        {
            var mn = new Point3d(0, 0, 0);
            var mx = new Point3d(20, 20, 20);
            // Ray misses the box by 2 units in X; radius 0 -> miss, radius 3 -> hit near face
            var onPlane = new Point3d(22, 10, 100);
            Check(!NavMath.AabbNearHit(onPlane, Vector3d.ZAxis, mn, mx).HasValue,
                  "thick: exact miss");
            Check(!NavMath.AabbNearHitThick(onPlane, Vector3d.ZAxis, mn, mx, 0.0).HasValue,
                  "thick: radius 0 still miss");
            var hit = NavMath.AabbNearHitThick(onPlane, Vector3d.ZAxis, mn, mx, 3.0);
            Check(hit.HasValue, "thick: radius 3 hits");
            CheckClose(hit.Value.Z, 20.0, 1e-9, "thick: near face of REAL box (not padded)");
            CheckClose(hit.Value.X, 22.0, 1e-9, "thick: stays under cursor X");
        }

        // --- CameraDepthScore: larger = closer to camera (VIEWDIR = target -> camera) -----
        {
            var vd = Vector3d.ZAxis;
            double front = NavMath.CameraDepthScore(new Point3d(0, 0, 20), vd);
            double back = NavMath.CameraDepthScore(new Point3d(0, 0, 0), vd);
            Check(front > back, "CameraDepthScore: front solid scores higher than back");
        }

        Console.WriteLine(s_failures == 0 ? "ALL PASS" : $"{s_failures} FAILURE(S)");
        Environment.Exit(s_failures == 0 ? 0 : 1);
    }
}
