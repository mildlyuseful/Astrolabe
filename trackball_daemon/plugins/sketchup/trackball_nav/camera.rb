# frozen_string_literal: true

# Camera math and pivot resolution for Trackball Nav.
#
# This module intentionally contains no socket or timer code. It runs on SketchUp's main
# thread and applies one already-scaled broker frame to the active view camera.
module TrackballNav
  module CameraDriver
    # Baseline orientation/feel only. The daemon has already applied the user's per-app
    # sensitivity, gain, and generic invert settings.
    ORBIT_SCALE = [-1.0, -1.0, 1.0].freeze
    PAN_SIGN = [-1.0, -1.0].freeze
    PAN_SCALE = 0.14
    ZOOM_SIGN = 1.0
    ZOOM_SCALE = 0.25
    FLY_MOVE = 0.5
    WALK_MOVE = 0.5

    WORLD_UP = Geom::Vector3d.new(0.0, 0.0, 1.0).freeze
    PIVOT_HOLD_IDLE = 0.35
    BBOX_MARGIN = 0.10
    EPSILON = 1.0e-9
    MIN_DISTANCE = 1.0e-4
    MIN_HEIGHT = 1.0e-4

    @gesture_time = 0.0
    @gesture_pivot = nil
    @object_cache = { model_id: nil, time: 0.0, point: nil }
    @last_scheme = nil
    # Under-mouse 'cursor' pivot: the last viewport pixel [x, y] (logical, view-relative), and an
    # optional refresh callable the live cursor tracker (cursor.rb) installs. camera.rb stays pure
    # (no Win32) so it is testable via tools/sketchup_nav_selftest.rb -- the tracker pushes the
    # pixel in; the self-test injects a synthetic one and leaves @cursor_refresh nil.
    @cursor_pixel = nil
    @cursor_refresh = nil

    class << self
      attr_accessor :cursor_pixel, :cursor_refresh

      def apply(frame, model = Sketchup.active_model, now = Time.now.to_f)
        return false unless model

        view = model.active_view
        return false unless view

        camera = view.camera
        return false unless camera

        orbit = numeric_array(frame['o'] || frame[:o], 3)
        pan = numeric_array(frame['p'] || frame[:p], 2)
        zoom = numeric(frame['z'] || frame[:z])
        pivot_id = (frame['op'] || frame[:op] || 'screen_center').to_s
        style = (frame['os'] || frame[:os] || 'free').to_s
        zoom_mode = (frame['zm'] || frame[:zm] || 'to_center').to_s
        advanced = frame['adv'] || frame[:adv] || {}
        advanced = {} unless advanced.is_a?(Hash)
        nav_mode = (advanced['nav_mode'] || 'orbit').to_s
        log_scheme(nav_mode, pivot_id, style, zoom_mode)

        has_orbit = orbit.any? { |value| value != 0.0 }
        has_pan = pan.any? { |value| value != 0.0 }
        has_zoom = zoom != 0.0
        return false unless has_orbit || has_pan || has_zoom

        idle = now - @gesture_time
        @gesture_time = now
        orbit, pan, zoom = apply_action_routing(nav_mode, pivot_id, orbit, pan, zoom, advanced)
        # Source routing can move a shifted input between pan and zoom (for example twist/Z ->
        # Walk Forward), so dispatch must use the routed channels rather than the pre-route flags.
        has_orbit = orbit.any? { |value| value != 0.0 }
        has_pan = pan.any? { |value| value != 0.0 }
        has_zoom = zoom != 0.0

        changed = case nav_mode
                  when 'fly'
                    invalidate_orbit_pivot! if has_pan || has_zoom
                    fly_camera(view, camera, orbit, pan, zoom, advanced)
                  when 'walk'
                    invalidate_orbit_pivot! if has_pan || has_zoom
                    walk_camera(view, camera, orbit, pan, zoom, advanced)
                  else
                    if has_orbit
                      orbit_camera(model, view, camera, orbit, pivot_id, style, idle,
                                   advanced['lock_horizon'] == true,
                                   advanced.fetch('selection_overrides_pivot', true) == true,
                                   advanced['orbit_pivot_candidates'] || [pivot_id])
                    elsif has_pan
                      invalidate_orbit_pivot!
                      pan_camera(view, camera, pan)
                    else
                      invalidate_orbit_pivot!
                      zoom_camera(model, camera, zoom, zoom_mode)
                    end
                  end
        view.invalidate if changed
        changed
      rescue StandardError => error
        TrackballNav.log_error('camera apply', error) if TrackballNav.respond_to?(:log_error)
        false
      end

      def reset_gesture!
        @gesture_time = 0.0
        @gesture_pivot = nil
      end

      def object_center(model, fallback)
        now = Time.now.to_f
        if @object_cache[:model_id] == model.object_id && @object_cache[:point] &&
           now - @object_cache[:time] < 0.5
          return @object_cache[:point].clone
        end

        bounds = model.bounds
        point = bounds && !bounds.empty? ? bounds.center : fallback
        @object_cache = { model_id: model.object_id, time: now, point: point.clone }
        point
      rescue StandardError
        fallback
      end

      def selection_center(model)
        bounds = Geom::BoundingBox.new
        found = false
        model.selection.each do |entity|
          next unless entity.respond_to?(:bounds)

          box = entity.bounds
          next if box.nil? || box.empty?

          bounds.add(box)
          found = true
        end
        found && !bounds.empty? ? bounds.center.clone : nil
      rescue StandardError
        nil
      end

      def screen_center_pivot(model, view)
        raytest_pixel(model, view, view.vpwidth * 0.5, view.vpheight * 0.5, 'screen-center-pivot')
      end

      # Raycast the surface under the LIVE MOUSE CURSOR (the same pick as screen_center_pivot,
      # aimed through @cursor_pixel instead of the centre). Returns a world Point3d or nil (no
      # cursor pixel / off-model), allowing the resolver to continue the configured chain. Half B of the
      # 'cursor' pivot; half A (getting @cursor_pixel) is the live tracker in cursor.rb.
      def cursor_pivot(model, view)
        px = @cursor_pixel
        return nil unless px.is_a?(Array) && px.length == 2

        raytest_pixel(model, view, px[0], px[1], 'cursor-pivot')
      end

      def raytest_pixel(model, view, x, y, label)
        ray = view.pickray(x, y)
        hit = ray && model.raytest(ray)
        point = hit && hit[0]
        return nil unless point && point_in_model_bounds?(point, model.bounds)

        TrackballNav.log_rate_limited(label, "#{label}: surface hit #{point.to_a.inspect}") if
          TrackballNav.respond_to?(:log_rate_limited)
        point.clone
      rescue StandardError => error
        TrackballNav.log_rate_limited("#{label}-err", "#{label} raytest failed: #{error.message}") if
          TrackballNav.respond_to?(:log_rate_limited)
        nil
      end

      private

      def orbit_camera(model, view, camera, orbit, pivot_id, style, idle, lock_horizon = false,
                       selection_overrides = true, candidates = nil)
        eye = camera.eye
        target = camera.target
        up = camera.up
        axes = camera_axes(eye, target, up)
        return false unless axes

        pivot = orbit_pivot(model, view, eye, target, pivot_id, idle, selection_overrides,
                            candidates)
        return false unless pivot

        if style == 'turntable' || lock_horizon
          yaw = orbit[1] * ORBIT_SCALE[1]
          pitch = orbit[0] * ORBIT_SCALE[0]
          eye, target, up = rotate_state(eye, target, up, pivot, WORLD_UP, yaw) if yaw != 0.0
          current_axes = camera_axes(eye, target, up)
          return false unless current_axes
          eye, target, up = rotate_state(eye, target, up, pivot, current_axes[0], pitch) if pitch != 0.0
          final_axes = camera_axes(eye, target, up, horizon: true)
        else
          pitch = orbit[0] * ORBIT_SCALE[0]
          yaw = orbit[1] * ORBIT_SCALE[1]
          roll = orbit[2] * ORBIT_SCALE[2]
          eye, target, up = rotate_state(eye, target, up, pivot, axes[0], pitch) if pitch != 0.0
          eye, target, up = rotate_state(eye, target, up, pivot, axes[1], yaw) if yaw != 0.0
          eye, target, up = rotate_state(eye, target, up, pivot, axes[2], roll) if roll != 0.0
          final_axes = camera_axes(eye, target, up)
        end
        return false unless final_axes
        return false if distance(eye, target) < MIN_DISTANCE

        camera.set(eye, target, final_axes[1])
        true
      end

      def fly_camera(view, camera, orbit, pan, zoom, advanced)
        if orbit.any? { |value| value != 0.0 }
          look_camera(camera, orbit, false)
        else
          move_camera(view, camera, pan, zoom, numeric(advanced['fly_speed'] || 1.0), false)
        end
      end

      def walk_camera(view, camera, orbit, pan, zoom, advanced)
        if orbit.any? { |value| value != 0.0 }
          look_camera(camera, orbit, true)
        else
          move_camera(view, camera, pan, zoom, numeric(advanced['walk_speed'] || 1.0), true)
        end
      end

      def look_camera(camera, orbit, horizon_lock)
        eye = camera.eye
        target = camera.target
        up = camera.up
        axes = camera_axes(eye, target, up)
        return false unless axes

        pitch = orbit[0] * ORBIT_SCALE[0]
        yaw = orbit[1] * ORBIT_SCALE[1]
        roll = horizon_lock ? 0.0 : orbit[2] * ORBIT_SCALE[2]
        if horizon_lock
          eye, target, up = rotate_state(eye, target, up, eye, WORLD_UP, yaw) if yaw != 0.0
          current_axes = camera_axes(eye, target, up)
          return false unless current_axes
          eye, target, up = rotate_state(eye, target, up, eye, current_axes[0], pitch) if pitch != 0.0
          final_axes = camera_axes(eye, target, up, horizon: true)
        else
          eye, target, up = rotate_state(eye, target, up, eye, axes[0], pitch) if pitch != 0.0
          eye, target, up = rotate_state(eye, target, up, eye, axes[1], yaw) if yaw != 0.0
          eye, target, up = rotate_state(eye, target, up, eye, axes[2], roll) if roll != 0.0
          final_axes = camera_axes(eye, target, up)
        end
        return false unless final_axes
        return false if distance(eye, target) < MIN_DISTANCE

        camera.set(eye, target, final_axes[1])
        true
      end

      def move_camera(view, camera, pan, zoom, speed, walk)
        eye = camera.eye
        target = camera.target
        axes = camera_axes(eye, target, camera.up)
        return false unless axes

        base = walk ? WALK_MOVE : FLY_MOVE
        scale = base * speed * [view_span(view, camera, eye, target), 1.0].max
        if walk
          right = horizontal_vector(axes[0])
          forward = horizontal_vector(axes[2])
          return false unless right && forward
          vertical = WORLD_UP
        else
          right, vertical, forward = axes
        end
        move = add_vectors(scale_vector(right, pan[0] * scale),
                           scale_vector(forward, pan[1] * scale))
        move = add_vectors(move, scale_vector(vertical, zoom * scale))
        return false if vector_length(move) < EPSILON

        camera.set(eye.offset(move), target.offset(move), axes[1])
        true
      end

      def pan_camera(view, camera, pan)
        eye = camera.eye
        target = camera.target
        axes = camera_axes(eye, target, camera.up)
        return false unless axes

        scale = view_span(view, camera, eye, target) * PAN_SCALE
        move = add_vectors(scale_vector(axes[0], PAN_SIGN[0] * pan[0] * scale),
                           scale_vector(axes[1], PAN_SIGN[1] * pan[1] * scale))
        return false if vector_length(move) < EPSILON

        camera.set(eye.offset(move), target.offset(move), axes[1])
        true
      end

      def zoom_camera(model, camera, amount, zoom_mode)
        eye = camera.eye
        target = camera.target
        axes = camera_axes(eye, target, camera.up)
        return false unless axes

        factor = 1.0 - ZOOM_SIGN * amount * ZOOM_SCALE
        factor = [[factor, 0.05].max, 20.0].min
        current_distance = distance(eye, target)
        factor = MIN_DISTANCE / current_distance if current_distance * factor < MIN_DISTANCE
        pivot = zoom_mode == 'to_object' ? object_center(model, target) : target

        if camera.perspective?
          new_eye = scale_point_about(pivot, eye, factor)
          new_target = scale_point_about(pivot, target, factor)
          return false if distance(new_eye, new_target) < MIN_DISTANCE

          camera.set(new_eye, new_target, axes[1])
        else
          new_target = scale_point_about(pivot, target, factor)
          move = new_target - target
          camera.set(eye.offset(move), new_target, axes[1])
          camera.height = [camera.height.to_f * factor, MIN_HEIGHT].max
        end
        true
      end

      def orbit_pivot(model, view, eye, target, pivot_id, idle, selection_overrides = true,
                      candidates = nil)
        selected = selection_center(model) if selection_overrides && pivot_id != 'camera'
        return selected if selected
        return @gesture_pivot.clone if @gesture_pivot && idle <= PIVOT_HOLD_IDLE

        (candidates || [pivot_id]).each do |method|
          point = case method
                  when 'camera' then eye.clone
                  when 'origin' then ORIGIN.clone
                  when 'selection' then selection_center(model)
                  when 'object' then object_center(model, nil)
                  when 'screen_center' then screen_center_pivot(model, view)
                  when 'cursor'
                    @cursor_refresh&.call(view)
                    cursor_pivot(model, view)
                  end
          if point
            @gesture_pivot = point
            return point.clone
          end
        end
        nil
      end

      def invalidate_orbit_pivot!
        @gesture_pivot = nil
      end

      def routed(values, sources, inversions, action, default)
        source = Integer(sources.fetch(action, default))
        source = default unless source.between?(0, 2)
        signed(values[source], inversions, action)
      rescue ArgumentError, TypeError
        signed(values[default], inversions, action)
      end

      def apply_action_routing(nav_mode, pivot_id, orbit, pan, zoom, advanced)
        invert = advanced['invert']
        invert = {} unless invert.is_a?(Hash)
        axes = advanced['axis_source']
        axes = {} unless axes.is_a?(Hash)
        rotation_inputs = orbit.dup
        movement = [pan[0], pan[1], zoom]
        case nav_mode
        when 'fly'
          config = invert['fly'] || {}
          source = axes['fly'] || {}
          orbit = [routed(rotation_inputs, source, config, 'pitch', 0),
                   routed(rotation_inputs, source, config, 'yaw', 1),
                   routed(rotation_inputs, source, config, 'bank', 2)]
          pan = [routed(movement, source, config, 'strafe', 0),
                 routed(movement, source, config, 'forward', 1)]
          zoom = routed(movement, source, config, 'vertical', 2)
        when 'walk'
          config = invert['walk'] || {}
          source = axes['walk'] || {}
          orbit = [routed(rotation_inputs, source, config, 'pitch', 0),
                   routed(rotation_inputs, source, config, 'yaw', 1), 0.0]
          pan = [routed(movement, source, config, 'strafe', 0),
                 routed(movement, source, config, 'forward', 1)]
          zoom = routed(movement, source, config, 'vertical', 2)
        else
          orbit_config = invert['orbit'] || {}
          orbit_source = axes['orbit'] || {}
          if pivot_id == 'camera'
            camera_invert = invert['camera'] || {}
            camera_source = axes['camera'] || {}
            orbit = [routed(rotation_inputs, camera_source, camera_invert, 'pitch', 0),
                     routed(rotation_inputs, camera_source, camera_invert, 'yaw', 1),
                     routed(rotation_inputs, camera_source, camera_invert, 'roll', 2)]
          else
            orbit = [routed(rotation_inputs, orbit_source, orbit_config, 'pitch', 0),
                     routed(rotation_inputs, orbit_source, orbit_config, 'yaw', 1),
                     routed(rotation_inputs, orbit_source, orbit_config, 'twist', 2)]
          end
          pan = [routed(movement, orbit_source, orbit_config, 'pan_x', 0),
                 routed(movement, orbit_source, orbit_config, 'pan_y', 1)]
          zoom = routed(movement, orbit_source, orbit_config, 'zoom', 2)
        end
        [orbit, pan, zoom]
      end

      def camera_axes(eye, target, up_hint, horizon: false)
        forward = target - eye
        return nil if vector_length(forward) < EPSILON
        forward.normalize!

        reference_up = horizon ? WORLD_UP : up_hint
        right = forward.cross(reference_up)
        if vector_length(right) < EPSILON
          right = forward.cross(up_hint)
        end
        return nil if vector_length(right) < EPSILON
        right.normalize!
        true_up = right.cross(forward)
        return nil if vector_length(true_up) < EPSILON
        true_up.normalize!
        [right, true_up, forward]
      end

      def rotate_state(eye, target, up, pivot, axis, angle)
        return [eye, target, up] if angle == 0.0 || vector_length(axis) < EPSILON

        transform = Geom::Transformation.rotation(pivot, axis, angle)
        [eye.transform(transform), target.transform(transform), up.transform(transform)]
      end

      def horizontal_vector(vector)
        horizontal = Geom::Vector3d.new(vector.x.to_f, vector.y.to_f, 0.0)
        return nil if vector_length(horizontal) < EPSILON

        horizontal.normalize!
        horizontal
      end

      def view_span(view, camera, eye, target)
        return [camera.height.to_f, MIN_HEIGHT].max unless camera.perspective?

        dist = [distance(eye, target), MIN_DISTANCE].max
        span = 2.0 * dist * Math.tan(camera.fov.to_f * Math::PI / 360.0)
        if camera.respond_to?(:fov_is_height?) && !camera.fov_is_height?
          aspect = view.vpheight.to_f > 0.0 ? view.vpwidth.to_f / view.vpheight.to_f : 1.0
          span /= aspect if aspect > EPSILON
        end
        [span, MIN_HEIGHT].max
      rescue StandardError
        [distance(eye, target), MIN_DISTANCE].max
      end

      def point_in_model_bounds?(point, bounds)
        return true unless bounds && !bounds.empty?

        margin = bounds.diagonal.to_f * BBOX_MARGIN
        min = bounds.min
        max = bounds.max
        point.x.to_f >= min.x.to_f - margin && point.x.to_f <= max.x.to_f + margin &&
          point.y.to_f >= min.y.to_f - margin && point.y.to_f <= max.y.to_f + margin &&
          point.z.to_f >= min.z.to_f - margin && point.z.to_f <= max.z.to_f + margin
      end

      def scale_point_about(pivot, point, factor)
        pivot.offset(scale_vector(point - pivot, factor))
      end

      def scale_vector(vector, factor)
        Geom::Vector3d.new(vector.x.to_f * factor, vector.y.to_f * factor, vector.z.to_f * factor)
      end

      def add_vectors(a, b)
        Geom::Vector3d.new(a.x.to_f + b.x.to_f, a.y.to_f + b.y.to_f, a.z.to_f + b.z.to_f)
      end

      def vector_length(vector)
        vector.length.to_f
      end

      def distance(a, b)
        a.distance(b).to_f
      end

      def numeric(value)
        Float(value || 0.0)
      rescue ArgumentError, TypeError
        0.0
      end

      def numeric_array(value, size)
        array = value.is_a?(Array) ? value : []
        Array.new(size) { |index| numeric(array[index]) }
      end

      def signed(value, config, key)
        config[key] == true ? -value : value
      end

      def log_scheme(nav_mode, pivot_id, style, zoom_mode)
        signature = [nav_mode, pivot_id, style, zoom_mode]
        return if signature == @last_scheme

        @last_scheme = signature
        TrackballNav.log("scheme: nav=#{nav_mode} pivot=#{pivot_id} style=#{style} zoom=#{zoom_mode}") if
          TrackballNav.respond_to?(:log)
      end
    end
  end
end
