// NavMath -- pure camera math for the GS (GraphicsSystem) transport.
//
// The GS view is a full camera (position/target/up/fieldWidth/fieldHeight), so every nav op --
// including free-orbit ROLL (encoded in the up vector), lens zoom, and camera dolly -- is one
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

        // DB VIEWTWIST equivalent of the camera's up vector. Used by the classic SetCurrentView
        // commit required by 2D Wireframe; see docs/apps/autocad.md, "2D Wireframe commit order
        // is crash-sensitive."
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
        // Pivots are optional; null preserves target-centered camera behavior:
        //   orbitPivot -- orbit rigidly about this WORLD point instead of the camera target: the
        //                 target rotates around it too, so the point keeps its screen position
        //                 (the "cursor" scheme -- the point under the mouse stays put).
        //   zoomPivot  -- zoom keeps this WORLD point's screen position fixed by scaling the
        //                 target and eye about it ("to_object" / "to_cursor"). This works in
        //                 both parallel and perspective projections.
        //   levelOnEntry -- one-shot transition flag: rebuild a LEVELED up before applying this
        //                 frame. Ordinary turntable frames pass false and preserve the established
        //                 horizon; this is never a continuous auto-level operation.
        public static CamState Apply(CamState c, double[] d, string style,
                                     double[] orbitSign, double panSignX, double panSignY,
                                     double panScale, double zoomSign, double zoomScale,
                                     Point3d? orbitPivot = null, Point3d? zoomPivot = null,
                                     bool levelOnEntry = false, string zoomStyle = "zoom")
        {
            var dir = c.Pos - c.Tgt;                     // target -> camera (out of the screen)
            double dist = dir.Length;
            if (dist < 1e-12) { dir = WorldUp; dist = 1.0; }
            else dir = dir / dist;
            var up = c.Up.GetNormal();
            var right = up.CrossProduct(dir);            // screen-right (right-handed basis)
            right = right.Length < 1e-9 ? Vector3d.XAxis : right.GetNormal();
            var tgt = c.Tgt;
            if (style == "turntable" && levelOnEntry)
            {
                var lvlRight = WorldUp.CrossProduct(dir);
                if (lvlRight.Length > 1e-9)
                {
                    right = lvlRight.GetNormal();
                    up = dir.CrossProduct(right).GetNormal();
                }
            }

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
                    up = up.TransformBy(m).GetNormal();       // preserve established horizon
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

            // --- zoom/dolly ------------------------------------------------------------------
            // Zoom changes the projection field in either projection. Dolly moves the eye along
            // its optical axis; as in AutoCAD's native camera model, that changes magnification
            // only in perspective (parallel cameras have no distance-based perspective scale).
            double fw = c.Fw, fh = c.Fh;
            if (d[5] != 0)
            {
                double factor = 1.0 + zoomSign * d[5] * zoomScale;
                if (factor > 1e-3)
                {
                    if (zoomStyle == "dolly")
                    {
                        if (c.Persp && zoomPivot.HasValue)
                        {
                            // Scale the target and eye about P. Reconstructing Pos below from the
                            // adjusted target and distance preserves the camera basis while P stays
                            // at the same screen coordinate.
                            var P = zoomPivot.Value;
                            tgt = P + (tgt - P) / factor;
                        }
                        dist /= factor;                 // factor > 1 dollies IN
                    }
                    else
                    {
                        fw /= factor; fh /= factor;
                        if (zoomPivot.HasValue)
                        {
                            // Narrowing the field magnifies the right/up offsets by factor. Shift
                            // the optical axis toward P by (1 - 1/factor) of its lateral offset so
                            // P stays at the same screen coordinate. Its depth is intentionally
                            // unchanged; this is a lens/field zoom, not a dolly.
                            var P = zoomPivot.Value;
                            var lateral = right * (P - tgt).DotProduct(right)
                                        + up * (P - tgt).DotProduct(up);
                            tgt += lateral * (1.0 - 1.0 / factor);
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

        // --- cursor-pivot depth helpers (shared with the offline NavMathTests twin) -------------
        // PointMonitor's ComputedPoint lies on the UCS construction plane: right under the cursor
        // in screen XY, but arbitrarily far in depth when the view is oblique. Sliding along the
        // view axis to the plane through `depthPoint` keeps the screen position and puts the
        // pivot at a scene-sensible depth (the view target / VIEWCTR).
        public static Point3d AtViewDepth(Point3d onRay, Vector3d viewDirUnit, Point3d depthPoint)
        {
            return onRay + viewDirUnit * (depthPoint - onRay).DotProduct(viewDirUnit);
        }

        // Keep a PointMonitor sample on the same screen-relative ray after the camera moves.
        // PointMonitor only fires when the physical mouse moves, so retaining its raw WCS point
        // across a trackball pan would make the next "fresh" cursor ray use the OLD view. Express
        // the sample on the old target plane as fractions of the old field, then rebuild it on the
        // new target plane/basis. The result is deliberately only a ray seed, never a depth hit.
        public static Point3d ReprojectScreenSample(Point3d sample, CamState before, CamState after)
        {
            static (Vector3d dir, Vector3d right, Vector3d up) Basis(CamState c)
            {
                var rawDir = c.Pos - c.Tgt;
                var dir = rawDir.Length < 1e-12 ? WorldUp : rawDir.GetNormal();
                var up = c.Up.Length < 1e-12 ? Vector3d.YAxis : c.Up.GetNormal();
                var right = up.CrossProduct(dir);
                right = right.Length < 1e-12 ? Vector3d.XAxis : right.GetNormal();
                up = dir.CrossProduct(right).GetNormal();
                return (dir, right, up);
            }

            var oldBasis = Basis(before);
            var oldPlane = AtViewDepth(sample, oldBasis.dir, before.Tgt);
            var offset = oldPlane - before.Tgt;
            double nx = Math.Abs(before.Fw) < 1e-12 ? 0.0
                : offset.DotProduct(oldBasis.right) / before.Fw;
            double ny = Math.Abs(before.Fh) < 1e-12 ? 0.0
                : offset.DotProduct(oldBasis.up) / before.Fh;
            var newBasis = Basis(after);
            return after.Tgt
                + newBasis.right * (nx * after.Fw)
                + newBasis.up * (ny * after.Fh);
        }

        // True when `p` lies inside the drawing extents grown by `marginFrac` of their diagonal.
        // Degenerate / unset extents (EXTMAX < EXTMIN, zero, or astronomical) → accept.
        public static bool InsideGrownExtents(Point3d p, Point3d mn, Point3d mx,
                                              double marginFrac = 0.10)
        {
            var d = mx - mn;
            if (!(mx.X >= mn.X && d.Length > 1e-9 && d.Length < 1e18))
                return true;
            double m = marginFrac * d.Length;
            return p.X >= mn.X - m && p.Y >= mn.Y - m && p.Z >= mn.Z - m &&
                   p.X <= mx.X + m && p.Y <= mx.Y + m && p.Z <= mx.Z + m;
        }

        // Near-face hit of the axis-aligned box [mn, mx] along the view ray through `onPlane`.
        // `viewDirUnit` is AutoCAD's VIEWDIR (target → camera). Returns null on a miss. Prefer
        // this over the bbox centre: the centre can land behind a solid from the camera, which
        // feels like orbiting about the back face.
        //
        // IMPORTANT: PointMonitor's ComputedPoint lies on the UCS construction plane, which often
        // CUTS THROUGH a solid (e.g. z=0 through a [0,10]^3 cube). Then onPlane is INSIDE the
        // AABB (tEnter < 0 < tExit). Always use tEnter — the camera-facing face — never t=0
        // (that returned an interior point with no coordinate on a face, which is wrong).
        public static Point3d? AabbNearHit(Point3d onPlane, Vector3d viewDirUnit,
                                          Point3d mn, Point3d mx)
        {
            // Travel into the scene (camera → target): p(t) = onPlane - t * viewDirUnit, t real.
            // Slab test; the near hit is tEnter (closest-to-camera intersection). When onPlane
            // is inside, tEnter is negative and still lands on the near face toward the camera.
            double tEnter = double.NegativeInfinity, tExit = double.PositiveInfinity;
            if (!Slab1D(onPlane.X, -viewDirUnit.X, mn.X, mx.X, ref tEnter, ref tExit) ||
                !Slab1D(onPlane.Y, -viewDirUnit.Y, mn.Y, mx.Y, ref tEnter, ref tExit) ||
                !Slab1D(onPlane.Z, -viewDirUnit.Z, mn.Z, mx.Z, ref tEnter, ref tExit) ||
                tExit < tEnter)
                return null;
            return onPlane - viewDirUnit * tEnter;
        }

        static bool Slab1D(double origin, double dir, double min, double max,
                           ref double tEnter, ref double tExit)
        {
            if (Math.Abs(dir) < 1e-15)
                return origin >= min && origin <= max;
            double inv = 1.0 / dir;
            double t0 = (min - origin) * inv, t1 = (max - origin) * inv;
            if (t0 > t1) { double tmp = t0; t0 = t1; t1 = tmp; }
            if (t0 > tEnter) tEnter = t0;
            if (t1 < tExit) tExit = t1;
            return tEnter <= tExit;
        }

        // Larger = closer to the camera (VIEWDIR points target → camera). Used to pick the
        // FRONT solid when GetPickedEntities returns several (order is not front-to-back).
        public static double CameraDepthScore(Point3d p, Vector3d viewDirUnit) =>
            p.X * viewDirUnit.X + p.Y * viewDirUnit.Y + p.Z * viewDirUnit.Z;

        // Thick-ray AABB probe: expand the box by `radius` on every axis, then near-hit. When
        // the exact ray misses but the cursor is within `radius` of the silhouette (2D Wireframe
        // edge picks, thin features), the expanded box still hits; depth is then taken from the
        // real box's near face under the cursor (screen position preserved). Mirrors Fusion's
        // aperture-expanding findBRepUsingRay.
        public static Point3d? AabbNearHitThick(Point3d onPlane, Vector3d viewDirUnit,
                                               Point3d mn, Point3d mx, double radius)
        {
            if (radius < 0.0) radius = 0.0;
            var pad = new Vector3d(radius, radius, radius);
            if (!AabbNearHit(onPlane, viewDirUnit, mn - pad, mx + pad).HasValue)
                return null;
            var exact = AabbNearHit(onPlane, viewDirUnit, mn, mx);
            if (exact.HasValue)
                return exact;
            // Grazing hit: stay under the cursor, use the real box's camera-facing corner depth.
            var nearCorner = new Point3d(
                viewDirUnit.X >= 0.0 ? mx.X : mn.X,
                viewDirUnit.Y >= 0.0 ? mx.Y : mn.Y,
                viewDirUnit.Z >= 0.0 ? mx.Z : mn.Z);
            return AtViewDepth(onPlane, viewDirUnit, nearCorner);
        }
    }
}
