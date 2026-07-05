// TrackballNav AutoCAD plugin -- the smooth-orbit half of the AutoCAD integration.
//
// WHY THIS EXISTS: over external COM automation, ANY change to AutoCAD's 3D view direction forces a
// full regen + ~34 ms of marshaling (verified exhaustively -- docs/apps/autocad.md
// 8.10/8.13). In-process there are two tiers (both verified live with a WorldDraw-counting
// DrawableOverrule, docs 8.15):
//
//   1. THE GS TRANSPORT (primary, regen-free): ObtainAcGsView(vpn, KernelDescriptor{"3D Drawing"})
//      returns the viewport's LIVE graphics-system view -- modern AutoCAD renders every visual
//      style, including "2D Wireframe", through the 3D kernel. Driving it with SetView + Update is
//      ~1.6 ms/frame with ZERO WorldDraw calls (no regen: the kernel re-renders its cached scene
//      graph under the new camera), and the DB stays untouched until a single
//      SetViewportFromView(vpn, view, regenRequired:false, rescaleRequired:false,
//      syncRequired:true) at gesture end -- which is ALSO regen-free. Free-roll rides in the up
//      vector. (GetCurrentAcGsView is a trap: it returns a DEFAULT camera disconnected from the
//      display, and SetViewFromViewport cannot seed it. The kernel-descriptor accessor is the one
//      native orbit uses.)
//
//   2. Editor.GetCurrentView()/SetCurrentView (fallback: paper space, GS failure): works
//      everywhere but regens EVERY call (~5-7 ms/frame with real entities) -- smooth-ish motion,
//      per-frame regeneration. This was the v0.1.x primary until the user noticed the model still
//      regenerating each frame; the WorldDraw counter confirmed it.
//
// This assembly is NETLOADed into acad.exe by the daemon. It connects to the trackball daemon's
// nav broker (127.0.0.1:47900 -- the same socket protocol as the Fusion/Blender/FreeCAD/Unreal
// add-ons), receives orbit/pan/zoom frames, and applies them per frame.
//
// Threading: a background socket thread only parses frames and accumulates deltas; a WinForms timer
// (created in Initialize, so it lives on AutoCAD's UI thread) drains and applies them -- the exact
// pattern of the FreeCAD add-on (socket thread + main-thread QTimer). All AutoCAD API calls happen
// on the UI thread.
//
// Diagnostics: %APPDATA%\TrackballDaemon\acad_plugin.log  +  TBNAV / TBNAVTEST commands in AutoCAD.

using System;
using System.IO;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcadApp = Autodesk.AutoCAD.ApplicationServices.Core.Application;
using AcUnique = Autodesk.AutoCAD.UniqueString;
using AcWindowApp = Autodesk.AutoCAD.ApplicationServices.Application;
using GsKernelDescriptor = Autodesk.AutoCAD.GraphicsSystem.KernelDescriptor;
using GsView = Autodesk.AutoCAD.GraphicsSystem.View;

[assembly: ExtensionApplication(typeof(TrackballNav.Plugin))]
[assembly: CommandClass(typeof(TrackballNav.Plugin))]

namespace TrackballNav
{
    public class Plugin : IExtensionApplication
    {
        public const string PluginVersion = "0.3.1";   // keep in sync with the csproj <Version>
                                                       // 0.3.1: scheme values renamed (pointer->cursor,
                                                       // to_pointer->to_cursor; daemon config v3).
                                                       // 0.3.0: "pointer" orbit pivot + "to_pointer"
                                                       // zoom (orbit/zoom about the point under the
                                                       // mouse, cached by an Editor.PointMonitor)
        const string BrokerHost = "127.0.0.1";
        const int BrokerPort = 47900;

        // --- baseline signs/scales (live-tune; the daemon's per-app bindings scale on top) -----
        static readonly double[] OrbitSign = { 1.0, 1.0, 1.0 };  // pitch(x), yaw(y), roll(z)
        const double PanSignX = -1.0, PanSignY = 1.0;  // target moves opposite the scene shift
        const double PanScale = 0.5;      // pan delta -> fraction of the view height
        const double ZoomSign = 1.0, ZoomScale = 0.5;
        const int TimerMs = 10;           // UI-thread drain cadence (~100 Hz ceiling)
        const int CommitIdleMs = 180;     // gesture considered over after this much frame silence

        readonly object _lock = new object();
        readonly double[] _acc = new double[6];
        string _opPivot = "view", _oStyle = "free", _zMode = "to_center";
        volatile bool _stop;
        volatile bool _connected;
        Thread _sockThread;
        TcpClient _tcp;
        System.Windows.Forms.Timer _timer;
        static string _log;
        static long _framesApplied;
        static long _gesturesCommitted;

        // --- GS gesture state (UI thread only) --------------------------------------------------
        GsView _gsView;
        int _gsVpn = -1;
        Document _gsDoc;
        bool _gsActive;
        bool _gsIs2D;                      // viewport is in the "2D Wireframe" visual style: the 2D
                                           // pipeline PRESENTS (our driven 3D view is only the
                                           // interaction view, like native 3DORBIT's), so the
                                           // commit must go through the classic SetCurrentView to
                                           // rebuild the 2D display list -- one regen per gesture.
        CamState _cam;                     // the SHADOW camera: single source of truth per gesture.
                                           // Never re-read from the view mid-gesture -- if AutoCAD
                                           // externally resets the GS view (mouse input etc.), the
                                           // next frame re-imposes the shadow and self-heals.
        DateTime _lastFrameAt;
        bool _regenPending;                // a 2D-wireframe commit wants a REGEN; fired as soon as
                                           // the editor is quiescent (never inject a command
                                           // string into an ACTIVE command)
        Document _regenDoc;                // the doc whose 2D cache is stale
        bool _regenInFlight;               // REGEN queued/executing: navigation is HELD until it
                                           // finishes -- REGEN rebuilds the kernel's views, and
                                           // driving a GS view across that is a native access
                                           // violation (v0.2.3 crashed AutoCAD exactly this way;
                                           // AVs are not catchable from managed code)
        DateTime _regenFiredAt;
        Document _regenWatchDoc;
        static volatile bool s_gsBroken;   // any GS failure -> legacy transport for the session
        static Plugin s_instance;          // the one IExtensionApplication (for diagnostics)

        // --- the live cursor cache (half A of the "cursor" pivot; UI thread only ----------------
        // PointMonitor fires on AutoCAD's UI thread, same as the timer, so no locking).
        // _ptrPoint = the WCS point under the mouse cursor, with the best depth available:
        // an active object snap > the picked entity's depth along the view ray > the raw
        // ComputedPoint (which lies on the UCS construction plane, NOT the 3D surface).
        Document _pmDoc;                   // doc whose Editor.PointMonitor we're subscribed to
        bool _ptrValid;
        Point3d _ptrPoint;
        DateTime _ptrAt;

        // "cursor"/"to_cursor" per-gesture holds: captured at the first orbit/zoom frame of a
        // gesture from the pointer cache (null = no/off-model cursor point -> plain target orbit),
        // then HELD so the pivot never chases a moving target. A pan/zoom frame invalidates the
        // orbit hold (the view moved under the cursor); orbit/pan invalidate the zoom hold; a
        // gesture end (EndGesture) resets both.
        Point3d? _heldOrbitPivot; bool _heldOrbitSet;
        Point3d? _heldZoomPivot;  bool _heldZoomSet;

        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();

        // --- lifecycle -------------------------------------------------------------------------
        public void Initialize()
        {
            s_instance = this;
            _log = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "TrackballDaemon", "acad_plugin.log");
            Log($"TrackballNavAcad v{PluginVersion} initializing (pid {Environment.ProcessId})");
            _sockThread = new Thread(SocketLoop) { IsBackground = true, Name = "tbnav-socket" };
            _sockThread.Start();
            _timer = new System.Windows.Forms.Timer { Interval = TimerMs };
            _timer.Tick += OnTick;
            _timer.Start();
        }

        public void Terminate()
        {
            _stop = true;
            try { EndGesture(commit: true); } catch { }
            try { UnhookRegen(); } catch { }
            try { EnsurePointMonitor(null); } catch { }
            try { _timer?.Stop(); } catch { }
            try { _tcp?.Close(); } catch { }
        }

        // --- diagnostics -----------------------------------------------------------------------
        [CommandMethod("TBNAV")]
        public static void StatusCommand()
        {
            var inst = s_instance;
            string ptr = "none yet";
            if (inst != null && inst._ptrValid)
                ptr = $"({inst._ptrPoint.X:0.##},{inst._ptrPoint.Y:0.##},{inst._ptrPoint.Z:0.##}) " +
                      $"{(DateTime.UtcNow - inst._ptrAt).TotalSeconds:0.0}s ago";
            var doc = AcadApp.DocumentManager.MdiActiveDocument;
            doc?.Editor.WriteMessage(
                $"\nTrackballNav v{PluginVersion}: broker={(s_instanceConnected ? "connected" : "not connected")}, " +
                $"transport={(s_gsBroken ? "legacy SetCurrentView (GS failed)" : "GS '3D Drawing' kernel (regen-free)")}, " +
                $"frames applied={Interlocked.Read(ref _framesApplied)}, " +
                $"gestures committed={Interlocked.Read(ref _gesturesCommitted)}, " +
                $"pointer={ptr}, log={_log}\n");
        }

        // Self-test: inject a short synthetic orbit through the EXACT production pipeline
        // (accumulate -> timer -> gesture -> idle commit), no broker/trackball needed. TBNAVTEST.
        [CommandMethod("TBNAVTEST")]
        public static void TestCommand()
        {
            s_testFramesLeft = 150;                    // ~1.5 s of motion at the 10 ms timer
            var doc = AcadApp.DocumentManager.MdiActiveDocument;
            doc?.Editor.WriteMessage("\nTrackballNav: synthetic orbit test started\n");
            Log("TBNAVTEST started");
        }

        // Dump the live pointer cache to the log (diagnosing "what does the plugin think is
        // under my mouse?" -- e.g. whether the entity-depth correction fired).
        [CommandMethod("TBNAVPTR")]
        public static void PointerDumpCommand()
        {
            var inst = s_instance;
            if (inst == null)
                return;
            Log(inst._ptrValid
                ? $"TBNAVPTR: cached ({inst._ptrPoint.X:0.###},{inst._ptrPoint.Y:0.###},{inst._ptrPoint.Z:0.###}) " +
                  $"{(DateTime.UtcNow - inst._ptrAt).TotalSeconds:0.0}s ago"
                : "TBNAVPTR: no cursor point cached");
            AcadApp.DocumentManager.MdiActiveDocument?.Editor.WriteMessage(
                "\nTrackballNav: pointer cache dumped to the log\n");
        }

        // Pointer-pivot self-test: seed a SYNTHETIC cursor point (no mouse needed), force the
        // "cursor" scheme, and run a synthetic orbit through the EXACT production pipeline
        // (accumulate -> timer -> gesture pivot hold -> NavMath -> GS drive -> idle commit).
        // The report asserts the pivot invariants on the gesture's seed/end shadow cameras:
        // rigid rotation about P (|tgt-P| and |pos-P| preserved), the target actually swept,
        // P's SCREEN position unchanged, and the committed DB target == the shadow target.
        // Triggered headless via COM SendCommand -- the same trick as TBNAVTEST.
        [CommandMethod("TBNAVPTRTEST")]
        public static void PointerTestCommand()
        {
            var inst = s_instance;
            var doc = AcadApp.DocumentManager.MdiActiveDocument;
            if (inst == null || doc == null)
                return;
            // a synthetic pivot INSIDE the drawing extents (off-centre so a target-orbit would
            // visibly move it); with no extents, offset from the current view centre
            Point3d P;
            var mn = SysPt("EXTMIN");
            var mx = SysPt("EXTMAX");
            var dg = mx - mn;
            if (mx.X >= mn.X && dg.Length > 1e-9 && dg.Length < 1e18)
                P = mn + dg * 0.75;
            else
                P = SysPt("VIEWCTR") +
                    new Vector3d(0.25, 0.10, 0.0) * Convert.ToDouble(AcadApp.GetSystemVariable("VIEWSIZE"));
            inst._ptrPoint = P;
            inst._ptrValid = true;
            inst._ptrAt = DateTime.UtcNow;
            inst._ptrTestPivot = P;
            inst._ptrTestSeeded = false;
            lock (inst._lock) { inst._opPivot = "cursor"; }
            s_ptrTestArm = true;
            s_ptrTestFramesLeft = 120;                 // ~1.2 s of motion at the 10 ms timer
            doc.Editor.WriteMessage($"\nTrackballNav: pointer-pivot test started (P=({P.X:0.##},{P.Y:0.##},{P.Z:0.##}))\n");
            Log($"TBNAVPTRTEST started, synthetic pivot ({P.X:0.###},{P.Y:0.###},{P.Z:0.###})");
        }

        static volatile int s_testFramesLeft;
        static volatile int s_ptrTestFramesLeft;
        static volatile bool s_ptrTestArm;         // capture the next gesture's seed + end shadows
        static volatile bool s_ptrTestReport;      // gesture finished -> evaluate + log once
        static volatile bool s_instanceConnected;
        CamState _ptrTestCam0, _ptrTestCamEnd;
        bool _ptrTestSeeded;
        Point3d _ptrTestPivot;

        static (Vector3d right, Vector3d up) CamBasis(CamState c)
        {
            var dir = c.Pos - c.Tgt;
            var dn = dir.Length < 1e-12 ? NavMath.WorldUp : dir.GetNormal();
            var up = c.Up.GetNormal();
            var right = up.CrossProduct(dn);
            right = right.Length < 1e-9 ? Vector3d.XAxis : right.GetNormal();
            return (right, up);
        }

        void ReportPtrTest()
        {
            try
            {
                if (!_ptrTestSeeded)
                {
                    Log("TBNAVPTRTEST FAIL: no gesture ran (no GS view? see earlier log lines)");
                    return;
                }
                _ptrTestSeeded = false;
                var P = _ptrTestPivot;
                var c0 = _ptrTestCam0;
                var c1 = _ptrTestCamEnd;
                double scale = 1.0 + c0.Fh;
                double dT0 = (c0.Tgt - P).Length, dT1 = (c1.Tgt - P).Length;
                double dP0 = (c0.Pos - P).Length, dP1 = (c1.Pos - P).Length;
                double tgtMoved = (c1.Tgt - c0.Tgt).Length;
                var b0 = CamBasis(c0);
                var b1 = CamBasis(c1);
                double rx0 = (P - c0.Tgt).DotProduct(b0.right), uy0 = (P - c0.Tgt).DotProduct(b0.up);
                double rx1 = (P - c1.Tgt).DotProduct(b1.right), uy1 = (P - c1.Tgt).DotProduct(b1.up);
                double dbErr = (SysPt("TARGET") - c1.Tgt).Length;
                bool pass = Math.Abs(dT1 - dT0) < 1e-6 * scale &&
                            Math.Abs(dP1 - dP0) < 1e-6 * scale &&
                            tgtMoved > 1e-4 * scale &&
                            Math.Abs(rx1 - rx0) < 1e-6 * scale &&
                            Math.Abs(uy1 - uy0) < 1e-6 * scale &&
                            dbErr < 1e-3 * scale;
                Log($"TBNAVPTRTEST {(pass ? "PASS" : "FAIL")}: |tgt-P| {dT0:0.####}->{dT1:0.####}, " +
                    $"|pos-P| {dP0:0.####}->{dP1:0.####}, tgt moved {tgtMoved:0.####}, " +
                    $"P screen offset ({rx0:0.####},{uy0:0.####})->({rx1:0.####},{uy1:0.####}), " +
                    $"db-target err {dbErr:0.####}");
                AcadApp.DocumentManager.MdiActiveDocument?.Editor.WriteMessage(
                    $"\nTrackballNav pointer test: {(pass ? "PASS" : "FAIL")} (details in the log)\n");
            }
            catch (System.Exception ex) { LogOnce("ptrtest-report", ex); }
        }

        static void Log(string msg)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(_log));
                File.AppendAllText(_log, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {msg}\r\n");
            }
            catch { }
        }

        // --- socket thread: connect to the nav broker, accumulate frames ------------------------
        void SocketLoop()
        {
            while (!_stop)
            {
                try
                {
                    using (var tcp = new TcpClient())
                    {
                        _tcp = tcp;
                        tcp.Connect(BrokerHost, BrokerPort);
                        using (var stream = tcp.GetStream())
                        {
                            var hello = JsonSerializer.Serialize(new
                            {
                                type = "hello",
                                app = "autocad",
                                version = PluginVersion,
                                pid = Environment.ProcessId,
                            });
                            var raw = Encoding.UTF8.GetBytes(hello + "\n");
                            stream.Write(raw, 0, raw.Length);
                            _connected = true;
                            s_instanceConnected = true;
                            Log("connected to nav broker");
                            using (var reader = new StreamReader(stream, Encoding.UTF8))
                            {
                                string line;
                                while (!_stop && (line = reader.ReadLine()) != null)
                                    HandleFrame(line);
                            }
                        }
                    }
                }
                catch { /* broker not up / connection dropped -> retry */ }
                if (_connected)
                    Log("disconnected from nav broker");
                _connected = false;
                s_instanceConnected = false;
                if (!_stop)
                    Thread.Sleep(2000);
            }
        }

        void HandleFrame(string line)
        {
            try
            {
                using (var doc = JsonDocument.Parse(line))
                {
                    var root = doc.RootElement;
                    if (!root.TryGetProperty("o", out var o))
                        return;
                    var p = root.GetProperty("p");
                    lock (_lock)
                    {
                        _acc[0] += o[0].GetDouble();
                        _acc[1] += o[1].GetDouble();
                        _acc[2] += o[2].GetDouble();
                        _acc[3] += p[0].GetDouble();
                        _acc[4] += p[1].GetDouble();
                        _acc[5] += root.GetProperty("z").GetDouble();
                        if (root.TryGetProperty("op", out var op)) _opPivot = op.GetString();
                        if (root.TryGetProperty("os", out var os)) _oStyle = os.GetString();
                        if (root.TryGetProperty("zm", out var zm)) _zMode = zm.GetString();
                    }
                }
            }
            catch { /* malformed frame -> skip */ }
        }

        // --- UI-thread timer: drain + apply ------------------------------------------------------
        void OnTick(object sender, EventArgs e)
        {
            // keep the PointMonitor bound to the ACTIVE document even while idle, so the pointer
            // cache is already warm when the first gesture of a session starts
            EnsurePointMonitor(AcadApp.DocumentManager.MdiActiveDocument);

            bool selfTest = s_testFramesLeft > 0 || s_ptrTestFramesLeft > 0;
            if (s_testFramesLeft > 0)
            {
                s_testFramesLeft--;
                lock (_lock)
                {
                    _acc[0] += 0.004;                 // gentle pitch + yaw sweep
                    _acc[1] += 0.010;
                }
            }
            else if (s_ptrTestFramesLeft > 0)
            {
                s_ptrTestFramesLeft--;
                lock (_lock)
                {
                    _acc[0] += 0.004;                 // same sweep, orbited about the test pivot
                    _acc[1] += 0.010;
                }
            }
            double[] delta = null;
            string style, opv, zmv;
            lock (_lock)
            {
                if (_acc[0] != 0 || _acc[1] != 0 || _acc[2] != 0 ||
                    _acc[3] != 0 || _acc[4] != 0 || _acc[5] != 0)
                {
                    delta = (double[])_acc.Clone();
                    Array.Clear(_acc, 0, 6);
                }
                style = _oStyle;
                opv = _opPivot;
                zmv = _zMode;
            }
            // watchdog: if the REGEN's CommandEnded never arrives, un-wedge navigation
            if (_regenInFlight && (DateTime.UtcNow - _regenFiredAt).TotalSeconds > 2.0)
                UnhookRegen();

            if (delta == null)
            {
                // gesture over? sync the driven camera into the DB exactly once
                if (_gsActive && (DateTime.UtcNow - _lastFrameAt).TotalMilliseconds > CommitIdleMs)
                    EndGesture(commit: true);
                else if (_regenPending && !_gsActive && !_regenInFlight)
                    FireDeferredRegen();
                else if (_nativeWatchArmed && !_regenPending && !_regenInFlight && ++_watchTick >= 5)
                {
                    _watchTick = 0;                    // ~50 ms cadence is plenty
                    CheckNativeViewChange();
                }
                if (s_ptrTestReport && !_gsActive && !_regenInFlight)
                {
                    s_ptrTestReport = false;
                    ReportPtrTest();                   // TBNAVPTRTEST: evaluate the finished gesture
                }
                return;
            }

            // A pending/executing REGEN and a GS gesture must NEVER overlap (native AV): hold the
            // frames -- put them back in the accumulator -- until the REGEN has fully finished.
            // The hold lasts one regen (~tens of ms); motion resumes with nothing lost.
            if (_regenPending || _regenInFlight)
            {
                if (_regenPending && !_regenInFlight)
                    FireDeferredRegen();               // typical: fires on the first held tick
                if (_regenPending || _regenInFlight)
                {
                    lock (_lock)
                        for (int i = 0; i < 6; i++)
                            _acc[i] += delta[i];
                    return;
                }
            }

            var doc = AcadApp.DocumentManager.MdiActiveDocument;
            if (doc == null)
                return;

            // Broker frames are broadcast to every add-on; only act when AutoCAD is the app the
            // user is actually in (the daemon gates too -- this is defence in depth). The self-test
            // bypasses the gate (it is triggered headless via COM SendCommand).
            try
            {
                if (!selfTest && GetForegroundWindow() != AcWindowApp.MainWindow.Handle)
                    return;
            }
            catch { }

            try
            {
                if (s_gsBroken || !TryApplyGs(doc, delta, style, opv, zmv))
                    Apply(doc, delta, style);          // legacy fallback (regens per frame;
                                                       // no pointer pivot -- view-centre orbit)
                _lastFrameAt = DateTime.UtcNow;
                Interlocked.Increment(ref _framesApplied);
            }
            catch (System.Exception ex)
            {
                LogOnce("apply", ex);
            }
        }

        static readonly System.Collections.Generic.HashSet<string> s_warned = new();
        static void LogOnce(string what, System.Exception ex)
        {
            if (s_warned.Add(what))
                Log($"{what} failed (further failures suppressed): {ex.GetType().Name}: {ex.Message}");
        }

        static readonly System.Collections.Generic.Dictionary<string, DateTime> s_rl = new();
        static void LogRL(string key, string msg, double periodSec = 2.0)   // UI thread only
        {
            var now = DateTime.UtcNow;
            if (s_rl.TryGetValue(key, out var t) && (now - t).TotalSeconds < periodSec)
                return;
            s_rl[key] = now;
            Log(msg);
        }

        // --- half A of the "cursor" pivot: the live cursor point via Editor.PointMonitor --------
        // A passive per-document subscription (the AutoCAD analogue of FreeCAD's SoLocation2Event
        // observer): the handler only reads the input context and caches one point. Kept bound to
        // the ACTIVE document from the timer, so the cache is warm before the first gesture.
        void EnsurePointMonitor(Document doc)
        {
            if (ReferenceEquals(_pmDoc, doc))
                return;
            if (_pmDoc != null)
            {
                try { _pmDoc.Editor.PointMonitor -= OnPointMonitor; } catch { }
                _pmDoc = null;
                _ptrValid = false;                 // the old doc's point is meaningless here
            }
            if (doc == null)
                return;
            try
            {
                doc.Editor.PointMonitor += OnPointMonitor;
                _pmDoc = doc;
                Log("pointer: PointMonitor subscribed on the active document");
            }
            catch (System.Exception ex) { LogOnce("pm-subscribe", ex); }
        }

        void OnPointMonitor(object sender, PointMonitorEventArgs e)
        {
            try
            {
                var ctx = e.Context;
                if (ctx == null || !ctx.PointComputed)
                    return;
                // ComputedPoint is WCS but sits on the UCS construction plane (or an osnap) --
                // the XY under the cursor is right, the DEPTH usually is not. Best available:
                Point3d pt = ctx.ComputedPoint;
                if ((ctx.History & PointHistoryBits.ObjectSnapped) != 0)
                {
                    pt = ctx.ObjectSnappedPoint;   // an osnap point lies ON the entity: true depth
                }
                else
                {
                    var picked = ctx.GetPickedEntities();
                    if (picked != null && picked.Length > 0)
                    {
                        var deep = SurfaceDepthPoint(picked, ctx.ComputedPoint);
                        if (deep.HasValue)
                            pt = deep.Value;
                    }
                }
                if (!_ptrValid)
                    Log($"pointer: first cursor point cached ({pt.X:0.###},{pt.Y:0.###},{pt.Z:0.###})");
                _ptrPoint = pt;
                _ptrValid = true;
                _ptrAt = DateTime.UtcNow;
            }
            catch (System.Exception ex) { LogOnce("pm-handler", ex); }
        }

        // Depth-correct the on-plane cursor point using the entity under the cursor: slide along
        // the view ray (which passes through ComputedPoint) to the entity's depth. Exact for
        // top-level curves (projected closest point); bbox-centre depth for everything else --
        // still exactly under the cursor (on the ray), at a plausible depth. Uses ids[0] (the
        // TOP-LEVEL entity): deeper path entries of a block are in block space, not WCS.
        Point3d? SurfaceDepthPoint(FullSubentityPath[] picked, Point3d onPlane)
        {
            try
            {
                var doc = _pmDoc;
                if (doc == null)
                    return null;
                var vd = SysPt("VIEWDIR").GetAsVector();      // target -> camera (view axis;
                if (vd.Length < 1e-12)                        // exact ray for parallel projection,
                    return null;                              // the axis approximation for persp)
                vd = vd.GetNormal();
                var ids = picked[0].GetObjectIds();
                if (ids == null || ids.Length == 0)
                    return null;
                using (var tr = doc.Database.TransactionManager.StartOpenCloseTransaction())
                {
                    var ent = tr.GetObject(ids[0], OpenMode.ForRead) as Entity;
                    if (ent == null)
                        return null;
                    if (ent is Curve cv)
                        return cv.GetClosestPointTo(onPlane, vd, false);
                    var ext = ent.GeometricExtents;
                    var ctr = ext.MinPoint + (ext.MaxPoint - ext.MinPoint) * 0.5;
                    return onPlane + vd * (ctr - onPlane).DotProduct(vd);
                }
            }
            catch { return null; }             // odd pick (erased id, zero extents) -> plane point
        }

        // The held "cursor" pivot: the cached cursor point, validated against the drawing
        // extents grown by 10% of their diagonal (a cursor over empty space intersects the UCS
        // plane arbitrarily far away when the view is oblique). null -> plain target orbit.
        Point3d? CapturePointerPivot()
        {
            if (!_ptrValid)
            {
                LogRL("ptr-none", "pointer pivot: no cursor point cached yet -> target orbit");
                return null;
            }
            var p = _ptrPoint;
            try
            {
                var mn = SysPt("EXTMIN");
                var mx = SysPt("EXTMAX");
                var d = mx - mn;
                if (mx.X >= mn.X && d.Length > 1e-9 && d.Length < 1e18)   // extents are real
                {
                    double m = 0.10 * d.Length;
                    if (p.X < mn.X - m || p.Y < mn.Y - m || p.Z < mn.Z - m ||
                        p.X > mx.X + m || p.Y > mx.Y + m || p.Z > mx.Z + m)
                    {
                        LogRL("ptr-oob",
                              "pointer pivot: cursor point outside the drawing extents -> target orbit");
                        return null;
                    }
                }
            }
            catch { }
            LogRL("ptr-hit",
                  $"pointer pivot: ({p.X:0.###},{p.Y:0.###},{p.Z:0.###}) held for the gesture");
            return p;
        }

        // --- the GS transport: live kernel view, regen-free (docs 8.15) -------------------------
        bool TryApplyGs(Document doc, double[] d, string style, string opv, string zmv)
        {
            try
            {
                if (!doc.Database.TileMode)
                    return false;                      // paper space -> legacy path
                int vpn = Convert.ToInt32(AcadApp.GetSystemVariable("CVPORT"));
                if (vpn < 2)
                    return false;
                if (_gsActive && (!ReferenceEquals(_gsDoc, doc) || _gsVpn != vpn))
                    EndGesture(commit: true);          // doc/viewport switched mid-stream
                if (!_gsActive)
                {
                    var gm = doc.GraphicsManager;
                    var desc = new GsKernelDescriptor();
                    desc.addRequirement(AcUnique.Intern("3D Drawing"));
                    var v = gm.ObtainAcGsView(vpn, desc);
                    if (v == null)
                        return false;                  // no live kernel view -> legacy
                    gm.SetViewFromViewport(v, vpn);    // seed with the on-screen camera
                    try { v.BeginInteractivity(60.0); } catch { }
                    _gsView = v; _gsVpn = vpn; _gsDoc = doc; _gsActive = true;
                    _gsIs2D = Is2DWireframe(doc, vpn);
                    _cam = NavMath.Read(v);            // seed the shadow ONCE per gesture
                    _heldOrbitSet = _heldZoomSet = false;   // fresh gesture -> fresh pivots
                    _heldOrbitPivot = _heldZoomPivot = null;
                    if (s_ptrTestArm && !_ptrTestSeeded)
                    {
                        _ptrTestCam0 = _cam;           // TBNAVPTRTEST: remember the seed camera
                        _ptrTestSeeded = true;
                    }
                    _nativeWatchArmed = false;         // this gesture's commit re-arms the watch
                    _nativeSettling = false;
                }
                else
                {
                    // diagnostic: did something reset the GS view since our last write?
                    var live = NavMath.Read(_gsView);
                    if ((live.Pos - _cam.Pos).Length > 1e-6 * (1.0 + _cam.Fh))
                        LogOnce("gs-clobber",
                                new System.Exception("GS view externally reset mid-gesture (self-healed from shadow)"));
                }
                // "cursor"/"to_cursor": resolve the held pivots (capture once, hold; see the
                // field comments). Perspective to_cursor falls back to the plain dolly.
                bool hasOrbit = d[0] != 0 || d[1] != 0 || d[2] != 0;
                bool hasPan = d[3] != 0 || d[4] != 0;
                bool hasZoom = d[5] != 0;
                Point3d? orbitPivot = null, zoomPivot = null;
                if (hasOrbit && opv == "cursor")
                {
                    if (!_heldOrbitSet) { _heldOrbitPivot = CapturePointerPivot(); _heldOrbitSet = true; }
                    orbitPivot = _heldOrbitPivot;
                }
                if (hasZoom && zmv == "to_cursor" && !_cam.Persp)
                {
                    if (!_heldZoomSet) { _heldZoomPivot = CapturePointerPivot(); _heldZoomSet = true; }
                    zoomPivot = _heldZoomPivot;
                }
                _cam = NavMath.Apply(_cam, d, style, OrbitSign, PanSignX, PanSignY,
                                     PanScale, ZoomSign, ZoomScale, orbitPivot, zoomPivot);
                NavMath.Write(_gsView, _cam);
                _gsView.Update();                      // repaint from the kernel cache: NO regen
                if (hasPan || hasZoom) _heldOrbitSet = false;   // view moved under the cursor ->
                if (hasOrbit || hasPan) _heldZoomSet = false;   // the next orbit/zoom re-captures
                return true;
            }
            catch (System.Exception ex)
            {
                LogOnce("gs-apply", ex);
                s_gsBroken = true;
                EndGesture(commit: false);
                return false;
            }
        }

        // Is the viewport in the "2D Wireframe" visual style? There the 2D pipeline PRESENTS and
        // keeps a view-dependent PROJECTED display list: our GS drive renders the interaction view
        // (exactly like native 3DORBIT), but a regen-free commit leaves that projected cache stale
        // and the next repaint re-presents the OLD view -- the user-reported snap-back.
        // (GetCurrent3dAcGsView is NOT a usable discriminator: it goes non-null permanently once
        // anyone -- including us -- has obtained the 3D view. Read the style type instead.)
        static bool Is2DWireframe(Document doc, int vpn)
        {
            try
            {
                var db = doc.Database;
                using (var tr = db.TransactionManager.StartOpenCloseTransaction())
                {
                    var vt = (ViewportTable)tr.GetObject(db.ViewportTableId, OpenMode.ForRead);
                    foreach (ObjectId id in vt)
                    {
                        var r = (ViewportTableRecord)tr.GetObject(id, OpenMode.ForRead);
                        if (r.Number != vpn)
                            continue;
                        var vs = (DBVisualStyle)tr.GetObject(r.VisualStyleId, OpenMode.ForRead);
                        return vs.Type == Autodesk.AutoCAD.GraphicsInterface.VisualStyleType.Wireframe2D;
                    }
                }
            }
            catch (System.Exception ex) { LogOnce("style-detect", ex); }
            return false;                              // unknown -> assume 3D (regen-free commit)
        }

        // Commit, two flavours:
        //  - 3D visual styles (the driven view IS the presentation): re-impose the SHADOW camera,
        //    SetViewportFromView(regenRequired:false), then belt-and-braces SetCurrentView(read-
        //    back) + UpdateTiledViewportsInDatabase. ZERO WorldDraws measured live (docs 8.15).
        //  - 2D Wireframe: build a ViewTableRecord from the SHADOW and push it through the classic
        //    ed.SetCurrentView while the current view still holds the OLD camera -- that is the
        //    one path that rebuilds the 2D projected display list (v0.1.x did it per frame; this
        //    does it ONCE per gesture -- docs 8.16).
        // A single commit failure can be transient (doc closed mid-gesture) -- the next gesture
        // just re-seeds from the DB; only a RELIABLY failing commit demotes to the legacy path.
        int _commitFailures;
        void EndGesture(bool commit)
        {
            if (!_gsActive)
                return;
            var v = _gsView; var doc = _gsDoc; int vpn = _gsVpn; var cam = _cam;
            bool is2D = _gsIs2D;
            _gsActive = false; _gsView = null; _gsDoc = null; _gsVpn = -1;
            _heldOrbitSet = _heldZoomSet = false;      // pointer pivots are per-gesture
            _heldOrbitPivot = _heldZoomPivot = null;
            if (s_ptrTestArm && _ptrTestSeeded)
            {
                _ptrTestCamEnd = cam;                  // TBNAVPTRTEST: the final shadow camera
                s_ptrTestArm = false;
                s_ptrTestReport = true;
            }
            try { v.EndInteractivity(); } catch { }
            if (!commit)
                return;
            try
            {
                var ed = doc.Editor;
                using (doc.LockDocument())
                {
                    if (is2D)
                    {
                        // THE ONE SAFE ORDER (crash-tested: 16 gesture cycles + interleaved
                        // native wheel zooms in a throwaway instance): write the shadow camera
                        // into the EXISTING *Active VPORT record(s), then IMMEDIATELY re-apply
                        // them with UpdateTiledViewportsFromDatabase. The pairing matters:
                        //  - UpdateTiledViewportsInDatabase ERASES+RECREATES the records ->
                        //    dangling kernel view -> AV next gesture (the v0.2.5 crash);
                        //  - field writes left UN-applied -> the same AV (the v0.2.7 crash);
                        //  - write + FromDatabase: the record is the SOURCE, so the record, the
                        //    editor view, and the display all agree -- and native wheel zoom
                        //    (which consults the record; the cause of the wireframe snap-back)
                        //    finally reads the right camera.
                        var db2 = doc.Database;
                        using (var tr = db2.TransactionManager.StartTransaction())
                        {
                            var vt = (ViewportTable)tr.GetObject(db2.ViewportTableId, OpenMode.ForRead);
                            foreach (ObjectId rid in vt)
                            {
                                var r = (ViewportTableRecord)tr.GetObject(rid, OpenMode.ForRead);
                                if (r.Number != vpn)
                                    continue;
                                r.UpgradeOpen();
                                r.ViewDirection = cam.Pos - cam.Tgt;
                                r.Target = cam.Tgt;
                                r.CenterPoint = new Point2d(0.0, 0.0);   // target IS the centre
                                r.Height = cam.Fh;
                                r.Width = cam.Fw;
                                r.ViewTwist = NavMath.TwistOf(cam);
                            }
                            tr.Commit();
                        }
                        ed.UpdateTiledViewportsFromDatabase();
                        // Belt-and-braces: one visible REGEN through the command pipeline (the
                        // projected display list is rebuilt for certain; HUD/ViewCube repaint),
                        // via the crash-safe interlock. The watch stays as final insurance.
                        _regenPending = true;
                        _regenDoc = doc;
                        ArmNativeWatch(doc);
                    }
                    else
                    {
                        NavMath.Write(v, cam);         // the shadow, not whatever the view holds
                        doc.GraphicsManager.SetViewportFromView(vpn, v, false, false, true);
                        using (var vtr = ed.GetCurrentView())
                            ed.SetCurrentView(vtr);    // editor-level commit (WD=0, same camera)
                        ed.UpdateTiledViewportsInDatabase();
                    }
                }
                Interlocked.Increment(ref _gesturesCommitted);
                _commitFailures = 0;
            }
            catch (System.Exception ex)
            {
                LogOnce("gs-commit", ex);
                if (++_commitFailures >= 3)
                    s_gsBroken = true;
            }
            if (_regenPending)
                FireDeferredRegen();                   // common case: fires immediately
        }

        // Queue the actual REGEN command (2D-wireframe display-list rebuild). Only when the
        // editor is quiescent (injected text would otherwise feed the ACTIVE command's prompt)
        // and never while a gesture is driving the GS view. CommandEnded tells us when the regen
        // is DONE so navigation can resume against a freshly obtained view.
        void FireDeferredRegen()
        {
            var doc = _regenDoc;
            try
            {
                if (doc == null || !ReferenceEquals(AcadApp.DocumentManager.MdiActiveDocument, doc))
                {
                    _regenPending = false;             // doc gone/switched: it regens on return
                    _regenDoc = null;
                    return;
                }
                if (_gsActive || !doc.Editor.IsQuiescent)
                    return;                            // retry on a later tick
                _regenPending = false;
                _regenDoc = null;
                _regenInFlight = true;
                _regenFiredAt = DateTime.UtcNow;
                _regenWatchDoc = doc;
                doc.CommandEnded += OnRegenDone;
                doc.CommandCancelled += OnRegenDone;
                doc.CommandFailed += OnRegenDone;
                doc.SendStringToExecute("_.REGEN ", true, false, false);
            }
            catch (System.Exception ex)
            {
                _regenPending = false;
                _regenDoc = null;
                UnhookRegen();
                LogOnce("regen", ex);
            }
        }

        void OnRegenDone(object sender, CommandEventArgs e)
        {
            if (string.Equals(e.GlobalCommandName, "REGEN", StringComparison.OrdinalIgnoreCase))
                UnhookRegen();
        }

        void UnhookRegen()
        {
            var doc = _regenWatchDoc;
            _regenWatchDoc = null;
            _regenInFlight = false;
            if (doc == null)
                return;
            try
            {
                doc.CommandEnded -= OnRegenDone;
                doc.CommandCancelled -= OnRegenDone;
                doc.CommandFailed -= OnRegenDone;
            }
            catch { }
        }

        // --- post-gesture native-op watch (2D wireframe only) -----------------------------------
        // Even with all camera records committed + the gesture-end REGEN, the FIRST native view
        // op after a trackball gesture can composite the model's cached projection against stale
        // state (user-observed: wheel zoom throws the wireframe to a wrong place/orientation; a
        // regen right AFTER repairs it, a preemptive one does not). So: watch for the first
        // non-trackball camera change after each 2D commit and queue ONE repair regen through the
        // same crash-safe interlock. One-shot; re-armed by the next gesture.
        //
        // PATIENCE MATTERS: the regen must behave like a human typing REGEN afterwards, never
        // fire into a native interaction still in progress (wheel-burst smooth transitions, MMB
        // drags -- the editor can report quiescent during those). So after the first change is
        // seen we wait until the camera has been STILL for ~400 ms AND no mouse button is down.
        bool _nativeWatchArmed;
        bool _nativeSettling;
        Document _nativeWatchDoc;
        Vector3d _watchDir, _lastDir;
        double _watchSize, _lastSize;
        Point3d _watchCtr, _lastCtr;
        int _watchTick;
        int _stableCount;

        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int vKey);
        static bool AnyMouseButtonDown() =>
            (GetAsyncKeyState(0x01) & 0x8000) != 0 ||   // left
            (GetAsyncKeyState(0x02) & 0x8000) != 0 ||   // right
            (GetAsyncKeyState(0x04) & 0x8000) != 0;     // middle (pan/orbit drags)

        static Point3d SysPt(string name)
        {
            var o = AcadApp.GetSystemVariable(name);
            return o is Point3d p ? p : Point3d.Origin;
        }

        void ArmNativeWatch(Document doc)
        {
            try
            {
                _watchDir = SysPt("VIEWDIR").GetAsVector();
                _watchSize = Convert.ToDouble(AcadApp.GetSystemVariable("VIEWSIZE"));
                _watchCtr = SysPt("VIEWCTR");
                _lastDir = _watchDir; _lastSize = _watchSize; _lastCtr = _watchCtr;
                _nativeWatchDoc = doc;
                _nativeSettling = false;
                _nativeWatchArmed = true;
            }
            catch { _nativeWatchArmed = false; }
        }

        static bool CamMoved(Vector3d d1, double s1, Point3d c1,
                             Vector3d d0, double s0, Point3d c0)
        {
            if (d1.Length < 1e-12 || d0.Length < 1e-12)
                return false;
            double eps = 1e-9 * (1.0 + s0);
            return d1.GetNormal().DotProduct(d0.GetNormal()) < 0.9999999 ||
                   Math.Abs(s1 - s0) > eps ||
                   (c1 - c0).Length > eps;
        }

        void CheckNativeViewChange()
        {
            try
            {
                var doc = AcadApp.DocumentManager.MdiActiveDocument;
                if (doc == null || !ReferenceEquals(doc, _nativeWatchDoc))
                {
                    _nativeWatchArmed = false; _nativeSettling = false;
                    return;
                }
                var dir = SysPt("VIEWDIR").GetAsVector();
                double size = Convert.ToDouble(AcadApp.GetSystemVariable("VIEWSIZE"));
                var ctr = SysPt("VIEWCTR");
                if (!_nativeSettling)
                {
                    if (CamMoved(dir, size, ctr, _watchDir, _watchSize, _watchCtr))
                    {
                        _nativeSettling = true;        // native op seen: wait for it to finish
                        _stableCount = 0;
                    }
                }
                else if (CamMoved(dir, size, ctr, _lastDir, _lastSize, _lastCtr))
                {
                    _stableCount = 0;                  // still moving (burst/transition/drag)
                }
                else if (++_stableCount >= 8           // ~400 ms of stillness
                         && !AnyMouseButtonDown()
                         && doc.Editor.IsQuiescent)
                {
                    _nativeWatchArmed = false; _nativeSettling = false;
                    _regenPending = true;              // ONE repair regen via the interlock
                    _regenDoc = doc;
                }
                _lastDir = dir; _lastSize = size; _lastCtr = ctr;
            }
            catch { _nativeWatchArmed = false; _nativeSettling = false; }
        }

        // --- legacy fallback: per-frame Editor.SetCurrentView (regens every call) ---------------
        void Apply(Document doc, double[] d, string style)
        {
            var ed = doc.Editor;
            using (var vtr = ed.GetCurrentView())
            {
                bool changed = false;
                if (d[0] != 0 || d[1] != 0 || d[2] != 0)
                {
                    OrbitView(vtr, d[0], d[1], d[2], style);
                    changed = true;
                }
                if (d[3] != 0 || d[4] != 0)
                {
                    // pan = shift the view centre in DCS (the view plane); scale by view height so
                    // the feel is zoom-independent
                    double f = vtr.Height * PanScale;
                    vtr.CenterPoint = new Point2d(
                        vtr.CenterPoint.X + PanSignX * d[3] * f,
                        vtr.CenterPoint.Y + PanSignY * d[4] * f);
                    changed = true;
                }
                if (d[5] != 0)
                {
                    double factor = 1.0 + ZoomSign * d[5] * ZoomScale;
                    if (factor > 1e-3)
                    {
                        vtr.Height /= factor;         // factor > 1 zooms IN
                        vtr.Width /= factor;
                        changed = true;
                    }
                }
                if (changed)
                    ed.SetCurrentView(vtr);
            }
        }

        // Rotate the view direction about the camera axes. free = composed camera-space axis, with
        // roll applied to the writable ViewTwist; turntable = yaw about WORLD Z + pitch about
        // camera-right, twist pinned level.
        void OrbitView(ViewTableRecord vtr, double ox, double oy, double oz, string style)
        {
            var dir = vtr.ViewDirection.GetNormal();          // target -> camera (out of screen)
            var right = NavMath.WorldUp.CrossProduct(dir);
            if (right.Length < 1e-9)
                right = Vector3d.XAxis;                        // top/bottom view fallback
            right = right.GetNormal();
            var up = dir.CrossProduct(right).GetNormal();

            double vx = OrbitSign[0] * ox;
            double vy = OrbitSign[1] * oy;
            double vz = OrbitSign[2] * oz;
            Vector3d newDir;
            if (style == "turntable")
            {
                var m = Matrix3d.Rotation(vy, NavMath.WorldUp, Point3d.Origin)
                      * Matrix3d.Rotation(vx, right, Point3d.Origin);
                newDir = dir.TransformBy(m);
                vtr.ViewTwist = 0.0;                           // turntable keeps the horizon level
            }
            else                                               // free
            {
                var axis = right * vx + up * vy + dir * -vz;   // -dir == camera forward
                double angle = axis.Length;
                if (angle > 1e-12)
                    newDir = dir.TransformBy(Matrix3d.Rotation(angle, axis / angle, Point3d.Origin));
                else
                    newDir = dir;
                if (vz != 0)
                    vtr.ViewTwist += vz;                       // free-roll via the writable ViewTwist
            }
            vtr.ViewDirection = newDir;
        }
    }
}
