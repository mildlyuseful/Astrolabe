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
        const string AddinVersion = "0.1.0";
        const int DefaultPort = 47900;
        const float PivotHoldIdle = 0.35f;
        const float ObjCacheSec = 0.5f;
        const float BboxMargin = 0.10f;

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
        static Vector2 _sceneMouseGui;   // last Scene GUI mouse (top-left origin), updated in duringSceneGui
        static bool _hasSceneMouse;

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
            if (e.type == EventType.MouseMove || e.type == EventType.MouseDrag ||
                e.type == EventType.MouseDown || e.type == EventType.Repaint)
            {
                _sceneMouseGui = e.mousePosition;
                _hasSceneMouse = true;
            }
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
            float flySpeed = MiniJson.Float(adv, "fly_speed", 1f);
            float walkSpeed = MiniJson.Float(adv, "walk_speed", 1f);
            var invert = MiniJson.Obj(adv, "invert") ?? new System.Collections.Generic.Dictionary<string, object>();

            var sig = $"{navMode}|{op}|{style}|{zm}|{twistAction}|{lockHorizon}|{selOverride}";
            if (sig != _lastScheme)
            {
                _lastScheme = sig;
                Log($"scheme: nav={navMode} pivot={op} style={style} zoom={zm} twist={twistAction} horizon={lockHorizon} sel_override={selOverride}");
            }

            ApplyInverts(navMode, op, ref o, ref p, ref z, invert);

            float size = Mathf.Max(sv.size, TrackballNavCamera.DistMin);
            var cam = TrackballNavCamera.FromSceneView(sv.pivot, sv.rotation, size);
            _focusDist = TrackballNavCamera.ClampDist(size);

            bool changed;
            if (navMode == "fly")
                changed = ApplyFly(ref cam, o, p, z, flySpeed);
            else if (navMode == "walk")
                changed = ApplyWalk(ref cam, o, p, z, walkSpeed);
            else
                changed = ApplyOrbit(ref cam, o, p, z, op, style, zm, twistAction, lockHorizon, panScales, idle, selOverride, sv);

            if (!changed) return;
            TrackballNavCamera.ToSceneView(cam, _focusDist, out var pivot, out var rot, out var newSize);
            sv.pivot = pivot;
            sv.rotation = rot;
            sv.size = newSize;
            sv.Repaint();
        }

        static bool ApplyOrbit(ref TrackballNavCamera.Cam cam, Vector3 o, Vector2 p, float z,
            string op, string style, string zm, string twistAction, bool lockHorizon,
            bool panScales, float idle, bool selOverride, SceneView sv)
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
                        TrackballNavCamera.Dolly(ref cam, twist, _focusDist, null);
                        _gestureInvalid = true;
                        did = true;
                    }
                }
                if (Mathf.Abs(orbitO.x) > 1e-12f || Mathf.Abs(orbitO.y) > 1e-12f || Mathf.Abs(orbitO.z) > 1e-12f)
                {
                    var pivot = OrbitPivot(op, cam, idle, selOverride, sv);
                    if (pivot.HasValue)
                        _focusDist = TrackballNavCamera.ClampDist((cam.Location - pivot.Value).magnitude);
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
                TrackballNavCamera.Dolly(ref cam, z, _focusDist, ZoomToward(zm, idle, selOverride, sv));
                return true;
            }
            return false;
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

        static Vector3? OrbitPivot(string op, TrackballNavCamera.Cam cam, float idle, bool selOverride, SceneView sv)
        {
            if (op == "viewpoint") return null;
            SelectionCenter(out var center, out var bbox);
            if (op == "object" || op == "selection")
                return center ?? ForwardPoint(cam);
            if (selOverride && center.HasValue) return center;
            if (op == "origin") return Vector3.zero;
            if (op == "view")
            {
                if (!_gesturePivot.HasValue || _gestureInvalid || idle > PivotHoldIdle)
                {
                    _gesturePivot = ScreenCenterPivot(cam, sv, selOverride ? bbox : null) ?? ForwardPoint(cam);
                    _gestureInvalid = false;
                }
                return _gesturePivot;
            }
            if (op == "cursor")
            {
                if (!_gesturePivot.HasValue || _gestureInvalid || idle > PivotHoldIdle)
                {
                    _gesturePivot = CursorPivot(sv, selOverride ? bbox : null) ?? ForwardPoint(cam);
                    _gestureInvalid = false;
                }
                return _gesturePivot;
            }
            return ForwardPoint(cam);
        }

        static Vector3? ZoomToward(string zm, float idle, bool selOverride, SceneView sv)
        {
            SelectionCenter(out var center, out var bbox);
            if (zm == "to_object") return center;
            if (zm == "to_cursor")
            {
                if (selOverride && center.HasValue) return center;
                if (!_zoomGesturePivot.HasValue || idle > PivotHoldIdle)
                    _zoomGesturePivot = CursorPivot(sv, selOverride ? bbox : null);
                return _zoomGesturePivot;
            }
            return null;
        }

        static Vector3 ForwardPoint(TrackballNavCamera.Cam cam)
        {
            float d = TrackballNavCamera.ClampDist(_focusDist);
            return cam.Location + cam.Forward * d;
        }

        static Vector3? ScreenCenterPivot(TrackballNavCamera.Cam cam, SceneView sv, Bounds? bbox)
        {
            var ray = new Ray(cam.Location, cam.Forward);
            return TraceRay(ray, bbox);
        }

        static Vector3? CursorPivot(SceneView sv, Bounds? bbox)
        {
            if (!_hasSceneMouse) return null;
            var cam = sv.camera;
            if (cam == null) return null;
            // Scene GUI Y is top-down; camera pixel Y is bottom-up.
            var sp = new Vector3(_sceneMouseGui.x, cam.pixelHeight - _sceneMouseGui.y, 0f);
            var ray = cam.ScreenPointToRay(sp);
            return TraceRay(ray, bbox);
        }

        static Vector3? TraceRay(Ray ray, Bounds? bbox)
        {
            if (Physics.Raycast(ray, out var hit, 1e7f))
            {
                if (bbox.HasValue && !InBbox(hit.point, bbox.Value)) return null;
                return hit.point;
            }
            // Editor picking without colliders: PickGameObject along the ray.
            try
            {
                var go = HandleUtility.PickGameObject(HandleUtility.WorldToGUIPoint(ray.origin + ray.direction), false);
                if (go != null)
                {
                    var r = go.GetComponent<Renderer>();
                    if (r != null)
                    {
                        var c = r.bounds.center;
                        if (bbox.HasValue && !InBbox(c, bbox.Value)) return null;
                        // Approximate surface: closest point on bounds along ray.
                        if (r.bounds.IntersectRay(ray, out float dist))
                            return ray.GetPoint(dist);
                        return c;
                    }
                }
            }
            catch { /* ignore */ }
            return null;
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
