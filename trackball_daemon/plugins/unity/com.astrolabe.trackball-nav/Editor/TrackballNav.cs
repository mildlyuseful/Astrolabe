// TrackballNav — Unity Editor Scene view add-on.
// Background socket thread reads the Trackball Daemon nav broker; EditorApplication.update
// drains frames on the main thread and drives SceneView (orbit / fly / walk).
using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEditor;
using UnityEngine;

namespace Astrolabe.TrackballNav
{
    [InitializeOnLoad]
    internal static class TrackballNav
    {
        const string AddinVersion = "0.1.7";
        const int DefaultPort = 47900;
        const float PivotHoldIdle = 0.35f;
        const float ObjCacheSec = 0.5f;
        const float SceneExtentCacheSec = 1.5f;
        const float BboxMargin = 0.10f;
        const float DefaultPivotExtentMult = 8f;
        const float MaxSceneSize = 1e7f;
        const int MaxTrisExact = 100000; // skip exact triangle tests on huge meshes (use bounds)

        static readonly ConcurrentQueue<string> Queue = new ConcurrentQueue<string>();
        static readonly object GestureLock = new object();
        static CancellationTokenSource _cts;
        static Thread _reader;
        static bool _started;
        static float _gestureT;
        static Vector3? _gesturePivot;
        static bool _gestureInvalid = true;
        static Vector3? _zoomGesturePivot;
        static float _focusDist = TrackballNavCamera.DistDefault;
        static double _objCacheT;
        static Vector3? _objCenter;
        static Bounds? _objBbox;
        static string _lastScheme;
        static Vector2 _sceneMouseGui;   // last Scene GUI mouse (top-left origin)
        static bool _hasSceneMouse;
        static Ray _cursorRay;           // world ray under the mouse (from GUIPointToWorldRay)
        static bool _hasCursorRay;
        // Hit under the mouse — resolved via Physics / IntersectRayMesh (no PlaceObject).
        static Vector3? _cursorHit;
        static bool _cursorHitValid;
        static double _lastCursorMissLog;
        static bool _dynClipOverridden;
        static bool? _savedDynClip;
        static float _pivotExtentMult = DefaultPivotExtentMult;
        static double _sceneExtentT;
        static float _sceneExtentR = 10f;

        static TrackballNav()
        {
            Start();
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
        }

        static void Start()
        {
            if (_started) return;
            _started = true;
            _cts = new CancellationTokenSource();
            _gestureT = 0f;
            _gesturePivot = null;
            _gestureInvalid = true;
            _zoomGesturePivot = null;
            _focusDist = TrackballNavCamera.DistDefault;
            _hasSceneMouse = false;
            _hasCursorRay = false;
            _cursorHit = null;
            _cursorHitValid = false;
            _dynClipOverridden = false;
            _savedDynClip = null;
            _pivotExtentMult = DefaultPivotExtentMult;
            _reader = new Thread(() => ReaderLoop(_cts.Token)) { IsBackground = true, Name = "trackball-nav-reader" };
            _reader.Start();
            EditorApplication.update += Pump;
            SceneView.duringSceneGui += OnSceneGui;
            Log($"start: TrackballNav v{AddinVersion}");
        }

        static void Stop()
        {
            if (!_started) return;
            _started = false;
            EditorApplication.update -= Pump;
            SceneView.duringSceneGui -= OnSceneGui;
            try { _cts?.Cancel(); } catch { /* ignore */ }
            Log($"stop: TrackballNav v{AddinVersion}");
        }

        static void OnSceneGui(SceneView sv)
        {
            var e = Event.current;
            if (e == null) return;
            // Mouse events only — never Layout/Repaint (nested OnGUI blanks the Scene view).
            if (e.type == EventType.MouseMove || e.type == EventType.MouseDrag ||
                e.type == EventType.MouseDown)
            {
                _sceneMouseGui = e.mousePosition;
                _hasSceneMouse = true;
                try
                {
                    // Correct Scene-view GUI → world ray (ScreenPointToRay Y mapping is wrong here).
                    _cursorRay = HandleUtility.GUIPointToWorldRay(e.mousePosition);
                    _hasCursorRay = true;
                }
                catch { return; }
                ResolveCursorHit(allowPickGameObject: true);
            }
        }

        // Physics + mesh raycast along the stored cursor ray. Safe from EditorApplication.update
        // (no PlaceObject). PickGameObject only when allowPickGameObject (mouse OnGUI).
        static void ResolveCursorHit(bool allowPickGameObject)
        {
            _cursorHit = null;
            _cursorHitValid = false;
            if (!_hasCursorRay) return;

            var ray = _cursorRay;
            float maxD = MaxPivotDistanceCached();

            if (Physics.Raycast(ray, out var ph, maxD, ~0, QueryTriggerInteraction.Ignore) &&
                IsValidPivotHit(ray, ph.point, maxD))
            {
                _cursorHit = ph.point;
                _cursorHitValid = true;
                return;
            }

            if (TryMeshRaycast(ray, maxD, out var meshPt) && IsValidPivotHit(ray, meshPt, maxD))
            {
                _cursorHit = meshPt;
                _cursorHitValid = true;
                return;
            }

            if (allowPickGameObject)
            {
                try
                {
                    var go = HandleUtility.PickGameObject(_sceneMouseGui, false);
                    if (go != null && TryRendererHit(go, ray, out var pt) && IsValidPivotHit(ray, pt, maxD))
                    {
                        _cursorHit = pt;
                        _cursorHitValid = true;
                    }
                }
                catch { /* ignore */ }
            }
        }

        // Re-cast from the update pump so trackball orbit works without moving the mouse.
        static void EnsureCursorHit(SceneView sv)
        {
            RefreshSceneExtentIfNeeded();
            if (!_hasCursorRay)
            {
                if (!_hasSceneMouse || sv == null || sv.camera == null) return;
                // Last-resort ray if we only have GUI coords (no prior GUIPointToWorldRay).
                var cam = sv.camera;
                _cursorRay = cam.ScreenPointToRay(new Vector3(_sceneMouseGui.x, cam.pixelHeight - _sceneMouseGui.y, 0f));
                _hasCursorRay = true;
            }
            ResolveCursorHit(allowPickGameObject: false);
        }

        static bool IsValidPivotHit(Ray ray, Vector3 point, float maxD)
        {
            if (float.IsNaN(point.x) || float.IsNaN(point.y) || float.IsNaN(point.z)) return false;
            var to = point - ray.origin;
            float along = Vector3.Dot(to, ray.direction);
            if (along < TrackballNavCamera.DistMin) return false;
            if (along > maxD) return false;
            if (to.magnitude > maxD * 1.01f) return false;
            return true;
        }

        // Editor meshes usually have no colliders. Prefer exact triangle hits; fall back to
        // renderer AABB. (HandleUtility.IntersectRayMesh is missing on some Unity builds.)
        static bool TryMeshRaycast(Ray ray, float maxD, out Vector3 point)
        {
            point = default;
            float bestExact = maxD;
            float bestBounds = maxD;
            bool haveExact = false;
            bool haveBounds = false;
            try
            {
#pragma warning disable CS0618
                var filters = UnityEngine.Object.FindObjectsOfType<MeshFilter>();
#pragma warning restore CS0618
                if (filters == null) return false;
                foreach (var mf in filters)
                {
                    if (mf == null || mf.sharedMesh == null) continue;
                    if (!mf.gameObject.activeInHierarchy) continue;
                    var rend = mf.GetComponent<Renderer>();
                    if (rend != null && !rend.enabled) continue;

                    float bd = maxD + 1f;
                    bool boundsHit = rend != null && rend.bounds.IntersectRay(ray, out bd) &&
                                     bd >= 0f && bd <= maxD;
                    if (!boundsHit && rend != null) continue;

                    if (boundsHit && bd < bestBounds)
                    {
                        bestBounds = bd;
                        haveBounds = true;
                        if (!haveExact)
                            point = ray.GetPoint(bd);
                    }

                    var mesh = mf.sharedMesh;
                    int triCount = mesh.triangles != null ? mesh.triangles.Length / 3 : 0;
                    if (triCount <= 0 || triCount > MaxTrisExact) continue;
                    if (TryRaycastMeshTriangles(ray, mesh, mf.transform.localToWorldMatrix, maxD,
                            out var exactPt, out float exactD) && exactD < bestExact)
                    {
                        bestExact = exactD;
                        point = exactPt;
                        haveExact = true;
                    }
                }
            }
            catch { /* ignore */ }
            return haveExact || haveBounds;
        }

        static bool TryRaycastMeshTriangles(Ray ray, Mesh mesh, Matrix4x4 localToWorld, float maxD,
            out Vector3 point, out float dist)
        {
            point = default;
            dist = maxD;
            int[] tris;
            Vector3[] verts;
            try
            {
                tris = mesh.triangles;
                verts = mesh.vertices;
            }
            catch { return false; }
            if (tris == null || verts == null || tris.Length < 3) return false;

            bool found = false;
            for (int i = 0; i + 2 < tris.Length; i += 3)
            {
                int i0 = tris[i], i1 = tris[i + 1], i2 = tris[i + 2];
                if ((uint)i0 >= (uint)verts.Length || (uint)i1 >= (uint)verts.Length ||
                    (uint)i2 >= (uint)verts.Length) continue;
                Vector3 v0 = localToWorld.MultiplyPoint3x4(verts[i0]);
                Vector3 v1 = localToWorld.MultiplyPoint3x4(verts[i1]);
                Vector3 v2 = localToWorld.MultiplyPoint3x4(verts[i2]);
                if (!RayTriangle(ray, v0, v1, v2, out float t)) continue;
                if (t < TrackballNavCamera.DistMin || t > dist || t > maxD) continue;
                dist = t;
                point = ray.GetPoint(t);
                found = true;
            }
            return found;
        }

        // Möller–Trumbore ray/triangle intersection.
        static bool RayTriangle(Ray ray, Vector3 v0, Vector3 v1, Vector3 v2, out float t)
        {
            t = 0f;
            const float eps = 1e-8f;
            Vector3 e1 = v1 - v0;
            Vector3 e2 = v2 - v0;
            Vector3 pvec = Vector3.Cross(ray.direction, e2);
            float det = Vector3.Dot(e1, pvec);
            if (det > -eps && det < eps) return false;
            float invDet = 1f / det;
            Vector3 tvec = ray.origin - v0;
            float u = Vector3.Dot(tvec, pvec) * invDet;
            if (u < 0f || u > 1f) return false;
            Vector3 qvec = Vector3.Cross(tvec, e1);
            float v = Vector3.Dot(ray.direction, qvec) * invDet;
            if (v < 0f || u + v > 1f) return false;
            t = Vector3.Dot(e2, qvec) * invDet;
            return t > eps;
        }

        // Refresh scene AABB from EditorApplication.update only (not during OnGUI).
        static void RefreshSceneExtentIfNeeded()
        {
            double now = EditorApplication.timeSinceStartup;
            if (now - _sceneExtentT < SceneExtentCacheSec) return;
            _sceneExtentT = now;
            Bounds? agg = null;
            try
            {
#pragma warning disable CS0618
                var renderers = UnityEngine.Object.FindObjectsOfType<Renderer>();
#pragma warning restore CS0618
                if (renderers != null)
                {
                    foreach (var r in renderers)
                    {
                        if (r == null || !r.enabled || !r.gameObject.activeInHierarchy) continue;
                        if (!agg.HasValue) agg = r.bounds;
                        else
                        {
                            var b = agg.Value;
                            b.Encapsulate(r.bounds);
                            agg = b;
                        }
                    }
                }
            }
            catch { /* ignore */ }
            if (agg.HasValue)
                _sceneExtentR = Mathf.Max(agg.Value.extents.magnitude, 0.1f);
            else
                _sceneExtentR = Mathf.Max(_focusDist, 1f);
        }

        static float MaxPivotDistanceCached()
        {
            float mult = Mathf.Max(_pivotExtentMult, 0.5f);
            return Mathf.Max(_sceneExtentR * mult, TrackballNavCamera.DistMin * 10f);
        }

        static float MaxPivotDistance()
        {
            RefreshSceneExtentIfNeeded();
            return MaxPivotDistanceCached();
        }

        static bool TryRendererHit(GameObject go, Ray ray, out Vector3 point)
        {
            point = default;
            float best = float.MaxValue;
            bool found = false;
            var renderers = go.GetComponentsInChildren<Renderer>();
            if (renderers != null)
            {
                foreach (var r in renderers)
                {
                    if (r == null) continue;
                    if (r.bounds.IntersectRay(ray, out float dist) && dist >= 0f && dist < best)
                    {
                        best = dist;
                        point = ray.GetPoint(dist);
                        found = true;
                    }
                }
            }
            return found;
        }

        static int BridgePort()
        {
            try
            {
                var appdata = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                var path = Path.Combine(appdata, "TrackballDaemon", "bridge.json");
                if (!File.Exists(path)) return DefaultPort;
                var json = File.ReadAllText(path);
                var m = System.Text.RegularExpressions.Regex.Match(json, "\"port\"\\s*:\\s*(\\d+)");
                if (m.Success && int.TryParse(m.Groups[1].Value, out var p)) return p;
            }
            catch { /* ignore */ }
            return DefaultPort;
        }

        static void ReaderLoop(CancellationToken token)
        {
            var port = BridgePort();
            var hello = $"{{\"type\":\"hello\",\"app\":\"unity\",\"version\":\"{AddinVersion}\",\"host\":\"{Application.unityVersion}\",\"pid\":{System.Diagnostics.Process.GetCurrentProcess().Id}}}\n";
            while (!token.IsCancellationRequested)
            {
                TcpClient client = null;
                try
                {
                    client = new TcpClient();
                    var ar = client.BeginConnect("127.0.0.1", port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(2000) || !client.Connected)
                    {
                        client.Close();
                        Thread.Sleep(1500);
                        continue;
                    }
                    client.EndConnect(ar);
                    var stream = client.GetStream();
                    var helloBytes = Encoding.UTF8.GetBytes(hello);
                    stream.Write(helloBytes, 0, helloBytes.Length);
                    stream.ReadTimeout = 500;
                    var buf = new byte[4096];
                    var acc = new StringBuilder();
                    while (!token.IsCancellationRequested)
                    {
                        int n;
                        try { n = stream.Read(buf, 0, buf.Length); }
                        catch (IOException) { continue; }
                        if (n <= 0) break;
                        acc.Append(Encoding.UTF8.GetString(buf, 0, n));
                        int nl;
                        while ((nl = IndexOfNewline(acc)) >= 0)
                        {
                            var line = acc.ToString(0, nl).Trim();
                            acc.Remove(0, nl + 1);
                            if (line.Length > 0) Queue.Enqueue(line);
                        }
                    }
                }
                catch
                {
                    Thread.Sleep(1500);
                }
                finally
                {
                    try { client?.Close(); } catch { /* ignore */ }
                }
            }
        }

        static int IndexOfNewline(StringBuilder sb)
        {
            for (int i = 0; i < sb.Length; i++)
                if (sb[i] == '\n') return i;
            return -1;
        }

        static void Pump()
        {
            if (Queue.IsEmpty) return;
            var sv = SceneView.lastActiveSceneView;
            if (sv == null) return;
            if (EditorApplication.isPlayingOrWillChangePlaymode) return;

            RefreshSceneExtentIfNeeded();

            float now = (float)EditorApplication.timeSinceStartup;
            float idle;
            lock (GestureLock)
            {
                idle = now - _gestureT;
                _gestureT = now;
            }

            while (Queue.TryDequeue(out var line))
            {
                try { Apply(sv, line, idle); }
                catch (Exception e) { Log($"apply error: {e.Message}"); }
                idle = 0f;
            }
        }

        static void Apply(SceneView sv, string line, float idle)
        {
            var frame = MiniJson.Parse(line);
            if (frame == null) return;
            var o = MiniJson.Vec3(frame, "o");
            var p = MiniJson.Vec2(frame, "p");
            float z = MiniJson.Float(frame, "z");
            string op = MiniJson.Str(frame, "op", "view");
            string style = MiniJson.Str(frame, "os", "free");
            string zm = MiniJson.Str(frame, "zm", "to_center");
            var adv = MiniJson.Obj(frame, "adv") ?? new System.Collections.Generic.Dictionary<string, object>();
            string navMode = MiniJson.Str(adv, "nav_mode", "orbit");
            bool lockHorizon = MiniJson.Bool(adv, "lock_horizon");
            string twistAction = MiniJson.Str(adv, "twist_action", "roll");
            bool panScales = MiniJson.Bool(adv, "pan_scales_with_distance", true);
            bool selOverride = MiniJson.Bool(adv, "selection_overrides_pivot", true);
            var pivotCandidates = MiniJson.StringList(adv, "orbit_pivot_candidates", op);
            float flySpeed = MiniJson.Float(adv, "fly_speed", 1f);
            float walkSpeed = MiniJson.Float(adv, "walk_speed", 1f);
            var invert = MiniJson.Obj(adv, "invert") ?? new System.Collections.Generic.Dictionary<string, object>();

            var sig = $"{navMode}|{op}|{style}|{zm}|{twistAction}|{lockHorizon}|{selOverride}|{MiniJson.Bool(adv, "override_dynamic_clip", true)}";
            if (sig != _lastScheme)
            {
                _lastScheme = sig;
                Log($"scheme: nav={navMode} pivot={op} style={style} zoom={zm} twist={twistAction} horizon={lockHorizon} sel_override={selOverride} override_dyn_clip={MiniJson.Bool(adv, "override_dynamic_clip", true)}");
            }

            ApplyInverts(navMode, op, ref o, ref p, ref z, invert);

            // SceneView.size is a fit-sphere radius, NOT eye→pivot distance.
            // Real distance is sv.cameraDistance (= size/sin(fov/2) in perspective).
            bool overrideDynClip = MiniJson.Bool(adv, "override_dynamic_clip", true);
            float extentMult = MiniJson.Float(adv, "pivot_extent_mult", DefaultPivotExtentMult);
            if (extentMult < 0.5f) extentMult = 0.5f;
            _pivotExtentMult = extentMult;
            MaybeOverrideDynamicClip(sv, overrideDynClip);

            float eyeDist = ReadEyeDistance(sv);
            var cam = TrackballNavCamera.FromSceneView(sv.pivot, sv.rotation, eyeDist);
            _focusDist = eyeDist;

            bool changed;
            if (navMode == "fly")
                changed = ApplyFly(ref cam, o, p, z, flySpeed);
            else if (navMode == "walk")
                changed = ApplyWalk(ref cam, o, p, z, walkSpeed);
            else
                changed = ApplyOrbit(ref cam, o, p, z, op, style, zm, twistAction, lockHorizon,
                    panScales, idle, selOverride, pivotCandidates, sv);

            if (!changed) return;
            TrackballNavCamera.ToSceneView(cam, _focusDist, out var pivot, out var rot, out _);
            WriteSceneView(sv, pivot, rot, _focusDist);
            sv.Repaint();
        }

        // Eye→pivot distance. Never use sv.size directly for navigation math.
        static float ReadEyeDistance(SceneView sv)
        {
            float d = Mathf.Abs(sv.cameraDistance);
            if (float.IsNaN(d) || d < TrackballNavCamera.DistMin)
                d = Mathf.Max(Mathf.Abs(sv.size), TrackballNavCamera.DistMin);
            return TrackballNavCamera.ClampDist(d);
        }

        // Write pivot/rotation/size so Unity's cameraDistance matches the intended eye distance.
        // Scale size by (newDist/oldDist) to preserve Unity's FOV/ortho conversion exactly.
        static void WriteSceneView(SceneView sv, Vector3 pivot, Quaternion rotation, float eyeDist)
        {
            eyeDist = TrackballNavCamera.ClampDist(eyeDist);
            if (float.IsNaN(pivot.x) || float.IsInfinity(pivot.x) ||
                float.IsNaN(eyeDist) || eyeDist < TrackballNavCamera.DistMin)
                return;

            float oldDist = Mathf.Abs(sv.cameraDistance);
            float oldSize = sv.size;
            if (float.IsNaN(oldSize) || float.IsInfinity(oldSize) || oldSize <= 0f ||
                float.IsNaN(oldDist) || oldDist < 1e-8f)
            {
                oldDist = eyeDist;
                oldSize = sv.orthographic ? eyeDist * 0.5f : eyeDist * 0.5f;
            }

            float newSize;
            if (oldDist > 1e-6f)
                newSize = oldSize * (eyeDist / oldDist);
            else if (sv.orthographic)
                newSize = eyeDist * 0.5f;
            else
            {
                float fov = 60f;
                try { fov = sv.cameraSettings.fieldOfView; } catch { /* ignore */ }
                float s = Mathf.Sin(fov * 0.5f * Mathf.Deg2Rad);
                newSize = s > 1e-6f ? eyeDist * s : eyeDist;
            }

            if (float.IsNaN(newSize) || float.IsInfinity(newSize) || newSize <= 0f)
                newSize = Mathf.Max(eyeDist * 0.5f, TrackballNavCamera.DistMin);
            newSize = Mathf.Clamp(newSize, TrackballNavCamera.DistMin * 0.01f, MaxSceneSize);

            sv.pivot = pivot;
            sv.rotation = rotation;
            sv.size = newSize;
        }

        // Scene View Camera → Dynamic Clipping: near/far = f(size). Feels like auto zoom-to-fit
        // when looking at different scales. Override forces fixed clip planes while navigating;
        // turning the override off restores the previous dynamicClip value.
        static void MaybeOverrideDynamicClip(SceneView sv, bool overrideOn)
        {
            try
            {
                var cs = sv.cameraSettings;
                if (cs == null) return;

                if (overrideOn)
                {
                    if (_dynClipOverridden) return;
                    if (!cs.dynamicClip) return; // already off — nothing to restore later
                    _savedDynClip = true;
                    float d = ReadEyeDistance(sv);
                    cs.dynamicClip = false;
                    cs.nearClip = Mathf.Clamp(d * 0.0005f, 0.01f, 10f);
                    cs.farClip = Mathf.Max(1000f, d * 2000f);
                    sv.cameraSettings = cs;
                    _dynClipOverridden = true;
                    Log($"dynamic-clip: overridden off (near={cs.nearClip:F3} far={cs.farClip:F0})");
                    return;
                }

                // Override disabled — restore if we were the ones who turned it off.
                if (_dynClipOverridden && _savedDynClip == true)
                {
                    cs.dynamicClip = true;
                    sv.cameraSettings = cs;
                    Log("dynamic-clip: restored on");
                }
                _dynClipOverridden = false;
                _savedDynClip = null;
            }
            catch (Exception e)
            {
                Log($"dynamic-clip override failed: {e.Message}");
            }
        }

        static bool ApplyOrbit(ref TrackballNavCamera.Cam cam, Vector3 o, Vector2 p, float z,
            string op, string style, string zm, string twistAction, bool lockHorizon,
            bool panScales, float idle, bool selOverride,
            System.Collections.Generic.List<string> pivotCandidates, SceneView sv)
        {
            if (Mathf.Abs(o.x) > 1e-12f || Mathf.Abs(o.y) > 1e-12f || Mathf.Abs(o.z) > 1e-12f)
            {
                float twist = o.z;
                var orbitO = new Vector3(o.x, o.y, 0f);
                bool did = false;
                if (Mathf.Abs(twist) > 1e-12f)
                {
                    if (twistAction == "roll" && !lockHorizon) orbitO.z = twist;
                    else if (twistAction == "zoom" || twistAction == "dolly")
                    {
                        ApplyDolly(ref cam, twist, null);
                        _gestureInvalid = true;
                        did = true;
                    }
                }
                if (Mathf.Abs(orbitO.x) > 1e-12f || Mathf.Abs(orbitO.y) > 1e-12f || Mathf.Abs(orbitO.z) > 1e-12f)
                {
                    var pivot = OrbitPivot(op, cam, idle, selOverride, pivotCandidates, sv);
                    if (!pivot.HasValue) return did;
                    if (pivot.HasValue)
                    {
                        float d = (cam.Location - pivot.Value).magnitude;
                        float maxD = MaxPivotDistance();
                        if (d > maxD)
                        {
                            var dir = (pivot.Value - cam.Location);
                            if (dir.sqrMagnitude > 1e-12f)
                                pivot = cam.Location + dir.normalized * maxD;
                            d = maxD;
                        }
                        _focusDist = TrackballNavCamera.ClampDist(d);
                    }
                    TrackballNavCamera.Orbit(ref cam, orbitO, style == "turntable" || lockHorizon, pivot);
                    _zoomGesturePivot = null;
                    return true;
                }
                return did;
            }
            if (Mathf.Abs(p.x) > 1e-12f || Mathf.Abs(p.y) > 1e-12f)
            {
                _gestureInvalid = true;
                _zoomGesturePivot = null;
                TrackballNavCamera.Pan(ref cam, p.x, p.y, panScales ? _focusDist : TrackballNavCamera.DistDefault);
                return true;
            }
            if (Mathf.Abs(z) > 1e-12f)
            {
                _gestureInvalid = true;
                ApplyDolly(ref cam, z, ZoomToward(zm, idle, selOverride, sv));
                return true;
            }
            return false;
        }

        // Dolly the eye, then update focus distance to the look-at that stayed put.
        // Without this, WriteSceneView keeps the old cameraDistance while the eye slides —
        // zoom looks mild, then the next orbit snaps focusDist to the real eye→surface distance.
        static void ApplyDolly(ref TrackballNavCamera.Cam cam, float z, Vector3? toward)
        {
            var lookAt = toward ?? (cam.Location + cam.Forward * TrackballNavCamera.ClampDist(_focusDist));
            TrackballNavCamera.Dolly(ref cam, z, _focusDist, toward);
            var toLook = lookAt - cam.Location;
            float along = Vector3.Dot(toLook, cam.Forward);
            // Prefer distance along view forward (eye→pivot); fall back to Euclidean if sideways.
            float dist = along > 1e-4f ? along : toLook.magnitude;
            _focusDist = TrackballNavCamera.ClampDist(dist);
        }

        static bool ApplyFly(ref TrackballNavCamera.Cam cam, Vector3 o, Vector2 p, float z, float speed)
        {
            if (Mathf.Abs(o.x) > 1e-12f || Mathf.Abs(o.y) > 1e-12f || Mathf.Abs(o.z) > 1e-12f)
            {
                TrackballNavCamera.Look(ref cam, o, false);
                return true;
            }
            if (Mathf.Abs(p.x) > 1e-12f || Mathf.Abs(p.y) > 1e-12f || Mathf.Abs(z) > 1e-12f)
            {
                TrackballNavCamera.FlyMove(ref cam, p, z, _focusDist, speed);
                _gestureInvalid = true;
                _zoomGesturePivot = null;
                return true;
            }
            return false;
        }

        static bool ApplyWalk(ref TrackballNavCamera.Cam cam, Vector3 o, Vector2 p, float z, float speed)
        {
            if (Mathf.Abs(o.x) > 1e-12f || Mathf.Abs(o.y) > 1e-12f || Mathf.Abs(o.z) > 1e-12f)
            {
                TrackballNavCamera.Look(ref cam, o, true);
                return true;
            }
            if (Mathf.Abs(p.x) > 1e-12f || Mathf.Abs(p.y) > 1e-12f || Mathf.Abs(z) > 1e-12f)
            {
                TrackballNavCamera.WalkMove(ref cam, p, z, _focusDist, speed);
                _gestureInvalid = true;
                _zoomGesturePivot = null;
                return true;
            }
            return false;
        }

        static Vector3? OrbitPivot(string op, TrackballNavCamera.Cam cam, float idle,
            bool selOverride, System.Collections.Generic.List<string> candidates, SceneView sv)
        {
            SelectionCenter(out var center, out var bbox);
            if (selOverride && op != "viewpoint" && center.HasValue) return center;
            if (_gesturePivot.HasValue && !_gestureInvalid && idle <= PivotHoldIdle)
                return _gesturePivot;
            foreach (var method in candidates)
            {
                Vector3? point = null;
                if (method == "viewpoint") point = cam.Location;
                else if (method == "origin") point = Vector3.zero;
                else if (method == "object" || method == "selection") point = center;
                else if (method == "view")
                    point = ScreenCenterPivot(cam, selOverride ? bbox : null);
                else if (method == "cursor")
                {
                    EnsureCursorHit(sv);
                    point = CursorPivot(selOverride ? bbox : null);
                    if (!point.HasValue) LogCursorMiss("orbit");
                }
                if (point.HasValue)
                {
                    _gesturePivot = point;
                    _gestureInvalid = false;
                    return point;
                }
            }
            return null;
        }

        static Vector3? ZoomToward(string zm, float idle, bool selOverride, SceneView sv)
        {
            SelectionCenter(out var center, out var bbox);
            if (zm == "to_object") return center;
            if (zm == "to_cursor")
            {
                if (selOverride && center.HasValue) return center;
                if (!_zoomGesturePivot.HasValue || idle > PivotHoldIdle)
                {
                    EnsureCursorHit(sv);
                    var hit = CursorPivot(selOverride ? bbox : null);
                    if (!hit.HasValue)
                        LogCursorMiss("zoom");
                    _zoomGesturePivot = hit;
                }
                return _zoomGesturePivot;
            }
            return null;
        }

        static Vector3 ForwardPoint(TrackballNavCamera.Cam cam)
        {
            float d = TrackballNavCamera.ClampDist(_focusDist);
            return cam.Location + cam.Forward * d;
        }

        static Vector3? ScreenCenterPivot(TrackballNavCamera.Cam cam, Bounds? bbox)
        {
            var ray = new Ray(cam.Location, cam.Forward);
            float maxD = MaxPivotDistance();
            if (Physics.Raycast(ray, out var hit, maxD, ~0, QueryTriggerInteraction.Ignore) &&
                IsValidPivotHit(ray, hit.point, maxD))
            {
                if (bbox.HasValue && !InBbox(hit.point, bbox.Value)) return null;
                return hit.point;
            }
            if (TryMeshRaycast(ray, maxD, out var meshPt) && IsValidPivotHit(ray, meshPt, maxD))
            {
                if (bbox.HasValue && !InBbox(meshPt, bbox.Value)) return null;
                return meshPt;
            }
            return null;
        }

        static Vector3? CursorPivot(Bounds? bbox)
        {
            if (!_hasSceneMouse) return null;
            if (!_cursorHitValid || !_cursorHit.HasValue) return null;
            if (bbox.HasValue && !InBbox(_cursorHit.Value, bbox.Value)) return null;
            return _cursorHit;
        }

        static void LogCursorMiss(string why)
        {
            double now = EditorApplication.timeSinceStartup;
            if (now - _lastCursorMissLog < 1.0) return;
            _lastCursorMissLog = now;
            Log($"cursor-pivot: nothing under cursor ({_sceneMouseGui.x:F0},{_sceneMouseGui.y:F0}) → view/forward fallback ({why})");
        }

        static bool InBbox(Vector3 p, Bounds b)
        {
            var e = b.extents * (1f + BboxMargin);
            var c = b.center;
            return Mathf.Abs(p.x - c.x) <= e.x && Mathf.Abs(p.y - c.y) <= e.y && Mathf.Abs(p.z - c.z) <= e.z;
        }

        static void SelectionCenter(out Vector3? center, out Bounds? bbox)
        {
            double now = EditorApplication.timeSinceStartup;
            if (_objCenter.HasValue && now - _objCacheT < ObjCacheSec)
            {
                center = _objCenter;
                bbox = _objBbox;
                return;
            }
            var transforms = Selection.transforms;
            if (transforms == null || transforms.Length == 0)
            {
                _objCenter = null;
                _objBbox = null;
                _objCacheT = now;
                center = null;
                bbox = null;
                return;
            }
            Bounds? agg = null;
            Vector3 sum = Vector3.zero;
            int n = 0;
            foreach (var t in transforms)
            {
                if (t == null) continue;
                var r = t.GetComponentInChildren<Renderer>();
                Bounds b = r != null ? r.bounds : new Bounds(t.position, Vector3.one * 0.1f);
                sum += b.center;
                n++;
                if (!agg.HasValue) agg = b;
                else
                {
                    var a = agg.Value;
                    a.Encapsulate(b);
                    agg = a;
                }
            }
            if (n == 0)
            {
                center = null;
                bbox = null;
                return;
            }
            _objCenter = sum / n;
            _objBbox = agg;
            _objCacheT = now;
            center = _objCenter;
            bbox = _objBbox;
        }

        static float Sgn(bool flag) => flag ? -1f : 1f;

        static void ApplyInverts(string navMode, string op, ref Vector3 o, ref Vector2 p, ref float z,
            System.Collections.Generic.Dictionary<string, object> inv)
        {
            if (navMode == "fly")
            {
                var f = MiniJson.Obj(inv, "fly") ?? new System.Collections.Generic.Dictionary<string, object>();
                o = new Vector3(o.x * Sgn(MiniJson.Bool(f, "pitch")), o.y * Sgn(MiniJson.Bool(f, "yaw")),
                    o.z * Sgn(MiniJson.Bool(f, "bank")));
                p = new Vector2(p.x * Sgn(MiniJson.Bool(f, "strafe")), p.y * Sgn(MiniJson.Bool(f, "forward")));
                z *= Sgn(MiniJson.Bool(f, "vertical"));
            }
            else if (navMode == "walk")
            {
                var w = MiniJson.Obj(inv, "walk") ?? new System.Collections.Generic.Dictionary<string, object>();
                o = new Vector3(o.x * Sgn(MiniJson.Bool(w, "pitch")), o.y * Sgn(MiniJson.Bool(w, "yaw")), o.z);
                p = new Vector2(p.x * Sgn(MiniJson.Bool(w, "strafe")), p.y * Sgn(MiniJson.Bool(w, "forward")));
                z *= Sgn(MiniJson.Bool(w, "vertical"));
            }
            else
            {
                var ob = MiniJson.Obj(inv, "orbit") ?? new System.Collections.Generic.Dictionary<string, object>();
                if (op == "viewpoint")
                {
                    var vp = MiniJson.Obj(inv, "viewpoint") ?? new System.Collections.Generic.Dictionary<string, object>();
                    o = new Vector3(o.x * Sgn(MiniJson.Bool(vp, "pitch")), o.y * Sgn(MiniJson.Bool(vp, "yaw")),
                        o.z * Sgn(MiniJson.Bool(vp, "roll")));
                }
                else
                {
                    o = new Vector3(o.x * Sgn(MiniJson.Bool(ob, "pitch")), o.y * Sgn(MiniJson.Bool(ob, "yaw")),
                        o.z * Sgn(MiniJson.Bool(ob, "twist")));
                }
                p = new Vector2(p.x * Sgn(MiniJson.Bool(ob, "pan_x")), p.y * Sgn(MiniJson.Bool(ob, "pan_y")));
                z *= Sgn(MiniJson.Bool(ob, "zoom"));
            }
        }

        static void Log(string msg)
        {
            try
            {
                var appdata = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                var dir = Path.Combine(appdata, "TrackballDaemon");
                Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "unity_addin.log"),
                    DateTime.Now.ToString("HH:mm:ss ") + msg + "\n");
            }
            catch { /* ignore */ }
        }
    }

    // Tiny JSON reader for the broker frame shape (no Newtonsoft dependency).
    internal static class MiniJson
    {
        public static System.Collections.Generic.Dictionary<string, object> Parse(string json)
        {
            int i = 0;
            return ParseObject(json, ref i) as System.Collections.Generic.Dictionary<string, object>;
        }

        public static System.Collections.Generic.Dictionary<string, object> Obj(
            System.Collections.Generic.Dictionary<string, object> d, string key)
        {
            if (d != null && d.TryGetValue(key, out var v) &&
                v is System.Collections.Generic.Dictionary<string, object> o) return o;
            return null;
        }

        public static string Str(System.Collections.Generic.Dictionary<string, object> d, string key, string def = "")
        {
            if (d != null && d.TryGetValue(key, out var v) && v != null) return v.ToString();
            return def;
        }

        public static float Float(System.Collections.Generic.Dictionary<string, object> d, string key, float def = 0f)
        {
            if (d == null || !d.TryGetValue(key, out var v) || v == null) return def;
            try { return Convert.ToSingle(v); } catch { return def; }
        }

        public static bool Bool(System.Collections.Generic.Dictionary<string, object> d, string key, bool def = false)
        {
            if (d == null || !d.TryGetValue(key, out var v) || v == null) return def;
            if (v is bool b) return b;
            if (v is string s) return s == "true" || s == "True" || s == "1";
            try { return Convert.ToBoolean(v); } catch { return def; }
        }

        public static System.Collections.Generic.List<string> StringList(
            System.Collections.Generic.Dictionary<string, object> d, string key, string fallback)
        {
            var result = new System.Collections.Generic.List<string>();
            if (d != null && d.TryGetValue(key, out var v) &&
                v is System.Collections.Generic.List<object> values)
                foreach (var item in values)
                    if (item != null) result.Add(item.ToString());
            if (result.Count == 0) result.Add(fallback);
            return result;
        }

        public static Vector3 Vec3(System.Collections.Generic.Dictionary<string, object> d, string key)
        {
            if (d == null || !d.TryGetValue(key, out var v) || !(v is System.Collections.Generic.List<object> a) || a.Count < 3)
                return Vector3.zero;
            return new Vector3(ToF(a[0]), ToF(a[1]), ToF(a[2]));
        }

        public static Vector2 Vec2(System.Collections.Generic.Dictionary<string, object> d, string key)
        {
            if (d == null || !d.TryGetValue(key, out var v) || !(v is System.Collections.Generic.List<object> a) || a.Count < 2)
                return Vector2.zero;
            return new Vector2(ToF(a[0]), ToF(a[1]));
        }

        static float ToF(object v)
        {
            try { return Convert.ToSingle(v); } catch { return 0f; }
        }

        static object ParseValue(string s, ref int i)
        {
            Skip(s, ref i);
            if (i >= s.Length) return null;
            char c = s[i];
            if (c == '{') return ParseObject(s, ref i);
            if (c == '[') return ParseArray(s, ref i);
            if (c == '"') return ParseString(s, ref i);
            if (c == 't' || c == 'f') return ParseBool(s, ref i);
            if (c == 'n') { i += 4; return null; }
            return ParseNumber(s, ref i);
        }

        static object ParseObject(string s, ref int i)
        {
            var d = new System.Collections.Generic.Dictionary<string, object>();
            i++; // {
            while (true)
            {
                Skip(s, ref i);
                if (i >= s.Length) break;
                if (s[i] == '}') { i++; break; }
                if (s[i] == ',') { i++; continue; }
                var key = ParseString(s, ref i);
                Skip(s, ref i);
                if (i < s.Length && s[i] == ':') i++;
                d[key] = ParseValue(s, ref i);
            }
            return d;
        }

        static object ParseArray(string s, ref int i)
        {
            var a = new System.Collections.Generic.List<object>();
            i++; // [
            while (true)
            {
                Skip(s, ref i);
                if (i >= s.Length) break;
                if (s[i] == ']') { i++; break; }
                if (s[i] == ',') { i++; continue; }
                a.Add(ParseValue(s, ref i));
            }
            return a;
        }

        static string ParseString(string s, ref int i)
        {
            i++; // "
            var sb = new StringBuilder();
            while (i < s.Length)
            {
                char c = s[i++];
                if (c == '"') break;
                if (c == '\\' && i < s.Length)
                {
                    char e = s[i++];
                    sb.Append(e == 'n' ? '\n' : e == 't' ? '\t' : e == 'r' ? '\r' : e);
                }
                else sb.Append(c);
            }
            return sb.ToString();
        }

        static object ParseNumber(string s, ref int i)
        {
            int start = i;
            if (i < s.Length && (s[i] == '-' || s[i] == '+')) i++;
            while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '.' || s[i] == 'e' || s[i] == 'E' || s[i] == '+' || s[i] == '-'))
                i++;
            var t = s.Substring(start, i - start);
            if (double.TryParse(t, System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out var d))
                return d;
            return 0.0;
        }

        static object ParseBool(string s, ref int i)
        {
            if (s.Substring(i).StartsWith("true")) { i += 4; return true; }
            if (s.Substring(i).StartsWith("false")) { i += 5; return false; }
            return false;
        }

        static void Skip(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
        }
    }
}
