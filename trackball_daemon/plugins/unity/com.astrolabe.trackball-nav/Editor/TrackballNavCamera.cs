// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

// Pure Scene-view camera math for Trackball Nav (no UnityEngine SceneView I/O here beyond
 // Vector3/Quaternion). Mirrors Unreal's tbnav_unreal_camera free-fly model: eye + orthonormal
 // basis; orbit/pan/dolly/fly/walk synthesised. Unity is LEFT-HANDED, Y-up, metres.
using System;
using UnityEngine;

namespace Astrolabe.TrackballNav
{
    internal static class TrackballNavCamera
    {
        public static readonly Vector3 OrbitScale = Vector3.one; // daemon supplies host baseline
        public static readonly Vector3 OrbitSign = new Vector3(1f, 1f, 1f);
        public static readonly Vector2 PanSign = Vector2.one;
        public const float PanScale = 1f;
        public const float ZoomSign = 1f;
        public const float ZoomScale = 1f;
        public const float MoveScale = 1f;
        public static readonly Vector3 WorldUp = Vector3.up;
        public const float DistDefault = 10f;
        public const float DistMin = 0.01f;
        public const float DistMax = 1e7f;

        public struct Cam
        {
            public Vector3 Location;
            public Vector3 Forward;
            public Vector3 Right;
            public Vector3 Up;

            public static Cam FromQuaternion(Vector3 location, Quaternion rotation)
            {
                return new Cam
                {
                    Location = location,
                    Forward = rotation * Vector3.forward,
                    Right = rotation * Vector3.right,
                    Up = rotation * Vector3.up,
                };
            }

            public Quaternion ToQuaternion()
            {
                return Quaternion.LookRotation(Forward, Up);
            }
        }

        public static float ClampDist(float dist)
        {
            if (float.IsNaN(dist) || dist <= 0f) return DistDefault;
            return Mathf.Clamp(dist, DistMin, DistMax);
        }

        static Vector3 RotateAboutAxis(Vector3 v, Vector3 axis, float angle)
        {
            float n = axis.magnitude;
            if (n < 1e-12f || Mathf.Abs(angle) < 1e-15f) return v;
            return Quaternion.AngleAxis(angle * Mathf.Rad2Deg, axis / n) * v;
        }

        // Remove existing roll: rebuild Right/Up so camera-right is horizontal (perpendicular to
        // WorldUp) while Forward is unchanged. The eye stays put, so the focus distance and the
        // synthesised orbit point (Location + Forward*dist) are preserved -- only the roll goes.
        // False in the degenerate straight-up/
        // straight-down view, where roll is indistinguishable from yaw.
        public static bool LevelHorizon(ref Cam cam)
        {
            var right = Vector3.Cross(WorldUp, cam.Forward);   // Unity: right = up x forward
            if (right.magnitude < 1e-6f) return false;
            right.Normalize();
            cam.Right = right;
            cam.Up = Vector3.Cross(cam.Forward, right).normalized;
            return true;
        }

        static void RotateFrame(ref Cam cam, Vector3 axis, float angle, Vector3? pivot)
        {
            cam.Forward = RotateAboutAxis(cam.Forward, axis, angle).normalized;
            cam.Right = RotateAboutAxis(cam.Right, axis, angle).normalized;
            cam.Up = RotateAboutAxis(cam.Up, axis, angle).normalized;
            if (pivot.HasValue)
            {
                var rel = cam.Location - pivot.Value;
                cam.Location = pivot.Value + RotateAboutAxis(rel, axis, angle);
            }
        }

        public static void Orbit(ref Cam cam, Vector3 o, bool turntable, Vector3? pivot)
        {
            float pitch = o.x * OrbitScale.x * OrbitSign.x;
            float yaw = o.y * OrbitScale.y * OrbitSign.y;
            float twist = o.z * OrbitScale.z * OrbitSign.z;
            if (turntable)
            {
                if (Mathf.Abs(yaw) > 1e-15f) RotateFrame(ref cam, WorldUp, yaw, pivot);
                if (Mathf.Abs(pitch) > 1e-15f) RotateFrame(ref cam, cam.Right, pitch, pivot);
                return;
            }
            var axis = cam.Right * pitch + cam.Up * yaw + cam.Forward * twist;
            float angle = axis.magnitude;
            if (angle > 1e-12f) RotateFrame(ref cam, axis, angle, pivot);
        }

        public static void Pan(ref Cam cam, float px, float py, float dist)
        {
            float k = PanScale * ClampDist(dist);
            float dx = PanSign.x * px * k;
            float dy = PanSign.y * py * k;
            cam.Location += cam.Right * dx + cam.Up * dy;
        }

        public static void Dolly(ref Cam cam, float z, float dist, Vector3? toward)
        {
            float k = ZoomSign * z * ZoomScale * ClampDist(dist);
            if (toward.HasValue)
            {
                var d = toward.Value - cam.Location;
                if (d.magnitude > 1e-6f)
                {
                    cam.Location += d.normalized * k;
                    return;
                }
            }
            cam.Location += cam.Forward * k;
        }

        public static void Look(ref Cam cam, Vector3 o, bool horizonLock)
        {
            Orbit(ref cam, o, horizonLock, null);
        }

        public static void FlyMove(ref Cam cam, Vector2 p, float z, float dist, float speed)
        {
            float k = MoveScale * speed * ClampDist(dist);
            cam.Location += cam.Right * (p.x * k) + cam.Forward * (p.y * k) + cam.Up * (z * k);
        }

        public static void WalkMove(ref Cam cam, Vector2 p, float z, float dist, float speed)
        {
            float k = MoveScale * speed * ClampDist(dist);
            var rh = Horizontal(cam.Right);
            var fh = Horizontal(cam.Forward);
            cam.Location += rh * (p.x * k) + fh * (p.y * k) + WorldUp * (z * k);
        }

        public static void RotateObject(ref Cam obj, Vector3 o, Cam view, Vector3 pivot)
        {
            var angles = Vector3.Scale(o, OrbitScale);
            var axes = new[] { view.Right, view.Up, view.Forward };
            var values = new[] { angles.x, angles.y, angles.z };
            for (int i = 0; i < axes.Length; i++)
                if (Mathf.Abs(values[i]) > 1e-15f)
                    RotateFrame(ref obj, axes[i], values[i], pivot);
        }

        public static Vector3 ObjectTranslation(Vector2 p, float z, Cam view, float dist)
        {
            float k = MoveScale * ClampDist(dist);
            return view.Right * (p.x * k) + view.Up * (p.y * k) + view.Forward * (z * k);
        }

        static Vector3 Horizontal(Vector3 v)
        {
            var h = new Vector3(v.x, 0f, v.z);
            return h.sqrMagnitude > 1e-12f ? h.normalized : Vector3.zero;
        }

        // SceneView stores pivot+size+rotation; size is a fit-sphere radius.
        // Callers must pass eye→pivot distance (SceneView.cameraDistance), not raw size.
        public static Cam FromSceneView(Vector3 pivot, Quaternion rotation, float size)
        {
            float dist = Mathf.Max(size, DistMin);
            var forward = rotation * Vector3.forward;
            var eye = pivot - forward * dist;
            return Cam.FromQuaternion(eye, rotation);
        }

        public static void ToSceneView(Cam cam, float focusDist, out Vector3 pivot, out Quaternion rotation, out float size)
        {
            float dist = ClampDist(focusDist);
            pivot = cam.Location + cam.Forward * dist;
            rotation = cam.ToQuaternion();
            size = dist;
        }
    }
}
