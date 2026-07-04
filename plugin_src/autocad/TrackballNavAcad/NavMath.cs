// NavMath -- pure camera math for the GS (GraphicsSystem) transport.
//
// The GS view is a full camera (position/target/up/fieldWidth/fieldHeight), so every nav op --
// including free-orbit ROLL (encoded in the up vector) and perspective zoom (a dolly) -- is one
// SetView call. This file is compiled verbatim into the live probe assemblies (TbProbeGs3) so the
// math that ships is exactly the math that was verified against a running AutoCAD.
using System;
using Autodesk.AutoCAD.Geometry;
using GsProjection = Autodesk.AutoCAD.GraphicsSystem.Projection;
using GsView = Autodesk.AutoCAD.GraphicsSystem.View;

namespace TrackballNav
{
    public struct CamState
    {
        public Point3d Pos, Tgt;
        public Vector3d Up;
        public double Fw, Fh;
        public bool Persp;
    }

    public static class NavMath
    {
        public static readonly Vector3d WorldUp = Vector3d.ZAxis;   // AutoCAD is Z-up

        public static CamState Read(GsView v) => new CamState
        {
            Pos = v.Position,
            Tgt = v.Target,
            Up = v.UpVector,
            Fw = v.FieldWidth,
            Fh = v.FieldHeight,
            Persp = v.IsPerspective,
        };

        public static void Write(GsView v, CamState c)
        {
            v.SetView(c.Pos, c.Tgt, c.Up, c.Fw, c.Fh,
                      c.Persp ? GsProjection.Perspective : GsProjection.Parallel);
        }

        // DB VIEWTWIST equivalent of the camera's up vector (sign verified live: a pure +0.30
        // NavMath roll landed as VIEWTWIST +0.3000 after commit). Used by the classic
        // SetCurrentView commit that 2D-wireframe viewports need (their 2D display list only
        // rebuilds through that path -- Plugin.EndGesture / notes 8.16).
        public static double TwistOf(CamState c)
        {
            var dir = c.Pos - c.Tgt;
            var d = dir.Length < 1e-12 ? WorldUp : dir.GetNormal();
            var lvlRight = WorldUp.CrossProduct(d);
            lvlRight = lvlRight.Length < 1e-9 ? Vector3d.XAxis : lvlRight.GetNormal();
            var lvlUp = d.CrossProduct(lvlRight).GetNormal();
            var up = c.Up.GetNormal();
            return Math.Atan2(up.DotProduct(lvlRight), up.DotProduct(lvlUp));
        }

        // d = {ox, oy, oz, px, py, z} (the broker frame, already daemon-scaled); signs/scales are
        // the caller's tuning constants.
        //
        // Pivots (both optional; the null defaults are the pre-0.3.0 behaviour exactly):
        //   orbitPivot -- orbit rigidly about this WORLD point instead of the camera target: the
        //                 target rotates around it too, so the point keeps its screen position
        //                 (the "pointer" scheme -- the point under the mouse stays put).
        //   zoomPivot  -- parallel zoom keeps this WORLD point's screen position fixed by sliding
        //                 the target toward it ("to_pointer"). Perspective zoom is a dolly toward
        //                 the target; holding an off-axis point fixed there would need an
        //                 off-axis dolly -- not supported, it falls back to the plain dolly.
        public static CamState Apply(CamState c, double[] d, string style,
                                     double[] orbitSign, double panSignX, double panSignY,
                                     double panScale, double zoomSign, double zoomScale,
                                     Point3d? orbitPivot = null, Point3d? zoomPivot = null)
        {
            var dir = c.Pos - c.Tgt;                     // target -> camera (out of the screen)
            double dist = dir.Length;
            if (dist < 1e-12) { dir = WorldUp; dist = 1.0; }
            else dir = dir / dist;
            var up = c.Up.GetNormal();
            var right = up.CrossProduct(dir);            // screen-right (right-handed basis)
            right = right.Length < 1e-9 ? Vector3d.XAxis : right.GetNormal();
            var tgt = c.Tgt;

            // --- orbit about the target (or, when orbitPivot is given, about that point) -------
            double vx = orbitSign[0] * d[0], vy = orbitSign[1] * d[1], vz = orbitSign[2] * d[2];
            if (vx != 0 || vy != 0 || vz != 0)
            {
                Matrix3d m;
                bool rotated = true;
                if (style == "turntable")
                {
                    m = Matrix3d.Rotation(vy, WorldUp, Point3d.Origin)
                      * Matrix3d.Rotation(vx, right, Point3d.Origin);
                    dir = dir.TransformBy(m).GetNormal();
                    var lvlRight = WorldUp.CrossProduct(dir);
                    if (lvlRight.Length > 1e-9)          // keep the horizon level
                        up = dir.CrossProduct(lvlRight.GetNormal()).GetNormal();
                    else
                        up = up.TransformBy(m).GetNormal();   // straight top/bottom: follow
                }
                else                                     // free: composed camera-space axis;
                {                                        // the -dir component IS the roll
                    var axis = right * vx + up * vy - dir * vz;
                    double ang = axis.Length;
                    if (ang > 1e-12)
                    {
                        m = Matrix3d.Rotation(ang, axis / ang, Point3d.Origin);
                        dir = dir.TransformBy(m).GetNormal();
                        up = up.TransformBy(m).GetNormal();
                    }
                    else { m = Matrix3d.Identity; rotated = false; }
                }
                if (rotated && orbitPivot.HasValue)
                {
                    // rigid rotation about P: the target sweeps around the pivot, and because the
                    // eye is rebuilt as tgt + dir*dist below, eye' = P + m*(eye - P) follows --
                    // the pivot keeps its exact screen position while the scene turns around it.
                    var P = orbitPivot.Value;
                    tgt = P + (tgt - P).TransformBy(m);
                }
                right = up.CrossProduct(dir);
                right = right.Length < 1e-9 ? Vector3d.XAxis : right.GetNormal();
            }

            // --- pan in the view plane (scaled by view height -> zoom-independent feel) -------
            if (d[3] != 0 || d[4] != 0)
            {
                double f = c.Fh * panScale;
                tgt += right * (panSignX * d[3] * f) + up * (panSignY * d[4] * f);
            }

            // --- zoom (parallel: shrink the field; perspective: dolly toward the target) ------
            double fw = c.Fw, fh = c.Fh;
            if (d[5] != 0)
            {
                double factor = 1.0 + zoomSign * d[5] * zoomScale;
                if (factor > 1e-3)
                {
                    if (c.Persp) dist /= factor;         // factor > 1 zooms IN
                    else
                    {
                        fw /= factor; fh /= factor;
                        if (zoomPivot.HasValue)
                        {
                            // keep P's screen position fixed: its offset from the optical axis
                            // must shrink by the same 1/factor the field does
                            var P = zoomPivot.Value;
                            tgt = P + (tgt - P) / factor;
                        }
                    }
                }
            }

            return new CamState
            {
                Pos = tgt + dir * dist,
                Tgt = tgt,
                Up = up,
                Fw = fw,
                Fh = fh,
                Persp = c.Persp,
            };
        }
    }
}
