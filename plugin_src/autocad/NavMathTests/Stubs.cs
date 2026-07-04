// Minimal stand-ins for the Autodesk types NavMath.cs touches, so it compiles verbatim outside
// acad.exe. Semantics match AutoCAD's managed geometry API: right-handed, column-vector rotation
// matrices (v' = M*v), A*B applies B first, TransformBy(M) = M*v, radians everywhere.
using System;

namespace Autodesk.AutoCAD.Geometry
{
    public readonly struct Vector3d
    {
        public double X { get; }
        public double Y { get; }
        public double Z { get; }

        public Vector3d(double x, double y, double z) { X = x; Y = y; Z = z; }

        public static Vector3d XAxis => new(1, 0, 0);
        public static Vector3d YAxis => new(0, 1, 0);
        public static Vector3d ZAxis => new(0, 0, 1);

        public double Length => Math.Sqrt(X * X + Y * Y + Z * Z);

        public Vector3d GetNormal()
        {
            double n = Length;
            return n < 1e-300 ? this : new Vector3d(X / n, Y / n, Z / n);
        }

        public double DotProduct(Vector3d v) => X * v.X + Y * v.Y + Z * v.Z;

        public Vector3d CrossProduct(Vector3d v) =>
            new(Y * v.Z - Z * v.Y, Z * v.X - X * v.Z, X * v.Y - Y * v.X);

        public Vector3d TransformBy(Matrix3d m) => m.Apply(this);

        public static Vector3d operator +(Vector3d a, Vector3d b) => new(a.X + b.X, a.Y + b.Y, a.Z + b.Z);
        public static Vector3d operator -(Vector3d a, Vector3d b) => new(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
        public static Vector3d operator -(Vector3d a) => new(-a.X, -a.Y, -a.Z);
        public static Vector3d operator *(Vector3d a, double s) => new(a.X * s, a.Y * s, a.Z * s);
        public static Vector3d operator *(double s, Vector3d a) => a * s;
        public static Vector3d operator /(Vector3d a, double s) => new(a.X / s, a.Y / s, a.Z / s);
    }

    public readonly struct Point3d
    {
        public double X { get; }
        public double Y { get; }
        public double Z { get; }

        public Point3d(double x, double y, double z) { X = x; Y = y; Z = z; }

        public static Point3d Origin => new(0, 0, 0);

        public static Vector3d operator -(Point3d a, Point3d b) => new(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
        public static Point3d operator +(Point3d p, Vector3d v) => new(p.X + v.X, p.Y + v.Y, p.Z + v.Z);
        public static Point3d operator -(Point3d p, Vector3d v) => new(p.X - v.X, p.Y - v.Y, p.Z - v.Z);
    }

    // Rotation-only 3x3 (NavMath only ever builds rotations about axes through the origin and
    // multiplies/applies them; the AutoCAD original is 4x4 but the translation part is unused).
    public readonly struct Matrix3d
    {
        readonly double m00, m01, m02, m10, m11, m12, m20, m21, m22;

        Matrix3d(double a, double b, double c, double d, double e, double f,
                 double g, double h, double i)
        { m00 = a; m01 = b; m02 = c; m10 = d; m11 = e; m12 = f; m20 = g; m21 = h; m22 = i; }

        public static Matrix3d Identity => new(1, 0, 0, 0, 1, 0, 0, 0, 1);

        // Rodrigues rotation about `axis` through `center` (NavMath always passes Origin, so the
        // translation component is irrelevant for the vector transforms it performs).
        public static Matrix3d Rotation(double angle, Vector3d axis, Point3d center)
        {
            var u = axis.GetNormal();
            double c = Math.Cos(angle), s = Math.Sin(angle), t = 1 - c;
            return new Matrix3d(
                t * u.X * u.X + c, t * u.X * u.Y - s * u.Z, t * u.X * u.Z + s * u.Y,
                t * u.X * u.Y + s * u.Z, t * u.Y * u.Y + c, t * u.Y * u.Z - s * u.X,
                t * u.X * u.Z - s * u.Y, t * u.Y * u.Z + s * u.X, t * u.Z * u.Z + c);
        }

        public Vector3d Apply(Vector3d v) => new(
            m00 * v.X + m01 * v.Y + m02 * v.Z,
            m10 * v.X + m11 * v.Y + m12 * v.Z,
            m20 * v.X + m21 * v.Y + m22 * v.Z);

        // A*B applied to v == A.Apply(B.Apply(v)) -- B first, then A (matches AutoCAD).
        public static Matrix3d operator *(Matrix3d a, Matrix3d b)
        {
            var c0 = a.Apply(new Vector3d(b.m00, b.m10, b.m20));
            var c1 = a.Apply(new Vector3d(b.m01, b.m11, b.m21));
            var c2 = a.Apply(new Vector3d(b.m02, b.m12, b.m22));
            return new Matrix3d(c0.X, c1.X, c2.X, c0.Y, c1.Y, c2.Y, c0.Z, c1.Z, c2.Z);
        }
    }
}

namespace Autodesk.AutoCAD.GraphicsSystem
{
    using Autodesk.AutoCAD.Geometry;

    public enum Projection { Parallel, Perspective }

    // Stand-in for the GS view: just enough surface for NavMath.Read/Write.
    public class View
    {
        public Point3d Position { get; set; }
        public Point3d Target { get; set; }
        public Vector3d UpVector { get; set; }
        public double FieldWidth { get; set; }
        public double FieldHeight { get; set; }
        public bool IsPerspective { get; set; }

        public void SetView(Point3d pos, Point3d tgt, Vector3d up,
                            double fw, double fh, Projection proj)
        {
            Position = pos; Target = tgt; UpVector = up;
            FieldWidth = fw; FieldHeight = fh;
            IsPerspective = proj == Projection.Perspective;
        }
    }
}
