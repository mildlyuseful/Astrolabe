# frozen_string_literal: true

# Live mouse-cursor tracker for the under-mouse 'cursor' orbit pivot (Half A).
#
# DESIGN DECISION (why not a passive tracking Tool): SketchUp's only in-API on-demand mouse
# source is Tool#onMouseMove, but a Tool is THE active interaction handler -- selecting one
# REPLACES the user's current tool (Select/Line/...), so the user can no longer click to draw or
# select while we track. That is an unacceptable UX regression for a background nav helper. There
# is no passive mouse observer in the SketchUp Ruby API (View/Model/Tools observers expose no
# mouse-move). So we read the OS cursor ON DEMAND with Win32 GetCursorPos via Fiddle -- the same
# non-hijacking approach the SolidWorks/Fusion cursor pivots use, and the pixel is always FRESH
# (read only at gesture start, when the pivot is (re)captured -- no per-tick cost, no stale cache).
#
# Screen px -> viewport px: WindowFromPoint(cursor) yields the graphics window; the viewport pixel
# is the cursor's FRACTION across that window's client rect times the logical viewport size
# (view.vpwidth/vpheight). The fraction is DPI-SCALE-FREE -- client_x/client_width cancels the
# logical-vs-physical factor -- so no per-monitor DPI handling is needed (unlike SolidWorks, whose
# IModelView.Transform forced it). Gated so a stray window (toolbar/tray/other app) is rejected:
# the window must sit under SketchUp's foreground frame AND share the viewport's aspect ratio, and
# the mapped pixel must land in-range; the raytest's bbox validation catches anything left.
#
# !! NEEDS LIVE-GUI VERIFY (SketchUp access was declined this session, so this is implemented to
# the API and unit-tested only for the offline pixel->pivot math -- tools/sketchup_nav_selftest.rb
# feeds a synthetic pixel). What a GUI pass must confirm: (1) WindowFromPoint over the drawing area
# returns the GL window whose client rect == the viewport (origin at its top-left, no inset);
# (2) the resulting pivot lands exactly under the cursor while orbiting. If (1) is off, the aspect
# gate + bbox validation degrade to the object-centre fallback rather than mispivoting. The
# `TrackballNav::CursorTracker.selftest(view)` helper prints the live mapping for that pass.

module TrackballNav
  module CursorTracker
    ASPECT_TOLERANCE = 0.04       # |win_aspect - vp_aspect| / vp_aspect allowed (rejects frame/tray)
    GA_ROOT = 2

    @enabled = false
    @fn = nil

    class << self
      # Wire the tracker into CameraDriver: it refreshes CameraDriver.cursor_pixel at gesture start.
      def install
        load_win32
        return false unless @enabled

        CameraDriver.cursor_refresh = ->(view) { CameraDriver.cursor_pixel = viewport_pixel(view) }
        TrackballNav.log('cursor: Win32 GetCursorPos tracker installed') if TrackballNav.respond_to?(:log)
        true
      rescue StandardError => error
        TrackballNav.log_error('cursor install', error) if TrackballNav.respond_to?(:log_error)
        false
      end

      # The live mouse cursor as a [vx, vy] viewport pixel (logical, view-relative), or nil when
      # the cursor isn't over the SketchUp drawing area / Win32 is unavailable. Called on demand at
      # gesture start (see CameraDriver#held_raycast_pivot), so its few Win32 calls never touch the
      # steady-state frame rate.
      def viewport_pixel(view)
        return nil unless @enabled && view

        sx, sy = cursor_pos
        return nil unless sx

        hwnd = window_from_point(sx, sy)
        return nil if null?(hwnd)
        return nil unless ancestor(hwnd, GA_ROOT).to_i == foreground_window.to_i   # under SketchUp

        cw, ch = client_size(hwnd)
        return nil if cw <= 0 || ch <= 0

        vpw = view.vpwidth.to_f
        vph = view.vpheight.to_f
        return nil if vpw <= 0.0 || vph <= 0.0

        vp_aspect = vpw / vph
        win_aspect = cw.to_f / ch.to_f
        return nil if ((win_aspect - vp_aspect) / vp_aspect).abs > ASPECT_TOLERANCE   # not the viewport

        cx, cy = screen_to_client(hwnd, sx, sy)
        vx = cx.to_f / cw * vpw          # DPI-scale-free: the client fraction cancels logical/physical
        vy = cy.to_f / ch * vph
        return nil unless vx >= 0.0 && vx <= vpw && vy >= 0.0 && vy <= vph

        [vx, vy]
      rescue StandardError => error
        TrackballNav.log_rate_limited('cursor-map', "cursor map failed: #{error.message}") if
          TrackballNav.respond_to?(:log_rate_limited)
        nil
      end

      # Print the live cursor mapping to the log (for the GUI verify pass): run
      #   TrackballNav::CursorTracker.selftest
      # in the Ruby Console, move the mouse over a face, and read %APPDATA%/TrackballDaemon/
      # sketchup_addin.log -- confirm the reported viewport pixel matches the cursor position and
      # that a pickray there hits what's under the mouse.
      def selftest(view = Sketchup.active_model.active_view)
        load_win32
        px = viewport_pixel(view)
        msg = px ? "cursor selftest: viewport pixel #{px.inspect} of (#{view.vpwidth},#{view.vpheight})" :
                   'cursor selftest: cursor not over the viewport (or Win32 unavailable)'
        TrackballNav.log(msg) if TrackballNav.respond_to?(:log)
        px
      end

      private

      def load_win32
        return if @enabled || @fn == :failed

        unless windows?
          @fn = :failed
          return
        end
        require 'fiddle'
        u = Fiddle.dlopen('user32')
        @fn = {
          cursor: Fiddle::Function.new(u['GetCursorPos'], [Fiddle::TYPE_VOIDP], Fiddle::TYPE_INT),
          wfp: Fiddle::Function.new(u['WindowFromPoint'], [Fiddle::TYPE_LONG_LONG], Fiddle::TYPE_VOIDP),
          rect: Fiddle::Function.new(u['GetClientRect'], [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP], Fiddle::TYPE_INT),
          s2c: Fiddle::Function.new(u['ScreenToClient'], [Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP], Fiddle::TYPE_INT),
          anc: Fiddle::Function.new(u['GetAncestor'], [Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT], Fiddle::TYPE_VOIDP),
          fg: Fiddle::Function.new(u['GetForegroundWindow'], [], Fiddle::TYPE_VOIDP)
        }
        @enabled = true
      rescue StandardError => error
        @fn = :failed
        @enabled = false
        TrackballNav.log_error('cursor win32 load', error) if TrackballNav.respond_to?(:log_error)
      end

      def windows?
        return Sketchup.platform == :platform_win if Sketchup.respond_to?(:platform)

        RUBY_PLATFORM =~ /mswin|mingw|cygwin/ ? true : false
      end

      def null?(handle)
        handle.nil? || handle.to_i.zero?
      end

      def cursor_pos
        buf = Fiddle::Pointer.malloc(8)
        return [nil, nil] if @fn[:cursor].call(buf).zero?

        buf[0, 8].unpack('l<2')
      end

      def window_from_point(sx, sy)
        packed = ((sy & 0xFFFFFFFF) << 32) | (sx & 0xFFFFFFFF)
        @fn[:wfp].call(packed)
      end

      def client_size(hwnd)
        r = Fiddle::Pointer.malloc(16)
        return [0, 0] if @fn[:rect].call(hwnd, r).zero?

        left, top, right, bottom = r[0, 16].unpack('l<4')
        [right - left, bottom - top]
      end

      def screen_to_client(hwnd, sx, sy)
        p = Fiddle::Pointer.malloc(8)
        p[0, 8] = [sx, sy].pack('l<2')
        @fn[:s2c].call(hwnd, p)
        p[0, 8].unpack('l<2')
      end

      def ancestor(hwnd, flag)
        @fn[:anc].call(hwnd, flag)
      end

      def foreground_window
        @fn[:fg].call
      end
    end
  end
end
