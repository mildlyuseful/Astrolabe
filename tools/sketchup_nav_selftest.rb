# frozen_string_literal: true

# Interactive SketchUp camera test. In SketchUp Desktop open:
#   Extensions -> Developer -> Ruby Console
# then run:
#   load 'C:/Users/dylan/Downloads/XIAO3389/tools/sketchup_nav_selftest.rb'
#
# This exercises the real production apply() path. SketchUp has no headless Ruby-API mode.
module TrackballNavSelfTest
  ROOT = File.expand_path('..', __dir__)
  CAMERA = File.join(ROOT, 'trackball_daemon', 'plugins', 'sketchup', 'trackball_nav', 'camera.rb')
  MAIN = File.join(ROOT, 'trackball_daemon', 'plugins', 'sketchup', 'trackball_nav', 'main.rb')
  EPSILON = 1.0e-5
  RESULT = File.join(ENV.fetch('TEMP', 'C:/tmp'), 'sketchup_nav_selftest.log')

  File.delete(RESULT) if File.exist?(RESULT)

  def self.report(message)
    puts message
    File.open(RESULT, 'a:utf-8') { |file| file.puts(message) }
  end

  def self.assert(label)
    unless yield
      report("FAIL #{label}")
      raise "FAIL #{label}"
    end

    report("PASS #{label}")
  end

  def self.distance(a, b)
    a.distance(b).to_f
  end

  def self.vector_length(vector)
    vector.length.to_f
  end

  def self.set_camera(camera, eye, target, perspective: true, height: 50.0)
    camera.perspective = perspective
    camera.height = height unless perspective
    camera.set(Geom::Point3d.new(eye), Geom::Point3d.new(target), Geom::Vector3d.new(0, 0, 1))
  end

  load CAMERA
  load MAIN
  TrackballNav.stop

  model = Sketchup.active_model
  raise 'FAIL no active SketchUp model' unless model

  view = model.active_view
  camera = view.camera
  original = {
    eye: camera.eye.clone,
    target: camera.target.clone,
    up: camera.up.clone,
    perspective: camera.perspective?,
    fov: camera.fov,
    height: camera.height
  }

  operation_open = false
  begin
    model.start_operation('Trackball Nav self-test', true)
    operation_open = true
    group = model.active_entities.add_group
    face = group.entities.add_face([0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0])
    raise 'FAIL could not create test face' unless face

    face.pushpull(10)
    box_center = group.bounds.center

    # Free orbit through the public apply() path. World-origin pivot must remain rigid.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    TrackballNav::CameraDriver.reset_gesture!
    before_eye = camera.eye.clone
    pivot_distance = distance(before_eye, ORIGIN)
    changed = TrackballNav.apply('o' => [0.12, 0.08, 0.04], 'p' => [0, 0], 'z' => 0,
                                 'op' => 'origin', 'os' => 'free', 'zm' => 'to_center')
    assert('free orbit changes camera') { changed && distance(before_eye, camera.eye) > EPSILON }
    assert('orbit about pivot holds pivot') do
      (distance(camera.eye, ORIGIN) - pivot_distance).abs < EPSILON
    end

    # Turntable ignores twist and keeps camera up orthogonal to its direction.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    changed = TrackballNav.apply('o' => [0.08, 0.10, 0.50], 'p' => [0, 0], 'z' => 0,
                                 'op' => 'origin', 'os' => 'turntable', 'zm' => 'to_center')
    assert('turntable orbit changes camera') { changed }
    assert('turntable camera basis remains orthogonal') do
      camera.direction.dot(camera.up).abs < EPSILON
    end

    # Viewpoint orbit rotates around the eye: position and focal distance stay fixed.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    before_eye = camera.eye.clone
    before_target = camera.target.clone
    before_distance = distance(camera.eye, camera.target)
    changed = TrackballNav.apply('o' => [0.10, 0.08, 0.04], 'p' => [0, 0], 'z' => 0,
                                 'op' => 'viewpoint', 'os' => 'free', 'zm' => 'to_center',
                                 'adv' => {'nav_mode' => 'orbit', 'invert' => {}})
    assert('viewpoint rotates camera in place') do
      changed && distance(before_eye, camera.eye) < EPSILON &&
        distance(before_target, camera.target) > EPSILON
    end
    assert('viewpoint preserves focal distance') do
      (distance(camera.eye, camera.target) - before_distance).abs < EPSILON
    end

    # Viewpoint's independent pitch invert must reverse the look direction.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    initial_direction = camera.direction.clone
    TrackballNav.apply('o' => [0.10, 0, 0], 'p' => [0, 0], 'z' => 0,
                       'op' => 'viewpoint', 'os' => 'free', 'zm' => 'to_center',
                       'adv' => {'nav_mode' => 'orbit', 'invert' => {'viewpoint' => {}}})
    normal_delta = camera.direction - initial_direction
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    TrackballNav.apply('o' => [0.10, 0, 0], 'p' => [0, 0], 'z' => 0,
                       'op' => 'viewpoint', 'os' => 'free', 'zm' => 'to_center',
                       'adv' => {'nav_mode' => 'orbit',
                                 'invert' => {'viewpoint' => {'pitch' => true}}})
    inverted_delta = camera.direction - initial_direction
    assert('viewpoint pitch invert reverses direction') { normal_delta.dot(inverted_delta) < 0.0 }

    # Fly look rotates around the eye; Shift channels provide strafe/forward/vertical movement.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    before_eye = camera.eye.clone
    before_target = camera.target.clone
    changed = TrackballNav.apply('o' => [0.08, 0.06, 0.04], 'p' => [0, 0], 'z' => 0,
                                 'op' => 'view', 'os' => 'free', 'zm' => 'to_center',
                                 'adv' => {'nav_mode' => 'fly', 'fly_speed' => 1.0,
                                           'invert' => {'fly' => {}}})
    assert('fly look turns in place') do
      changed && distance(before_eye, camera.eye) < EPSILON &&
        distance(before_target, camera.target) > EPSILON
    end
    before_eye = camera.eye.clone
    before_target = camera.target.clone
    changed = TrackballNav.apply('o' => [0, 0, 0], 'p' => [0.10, 0.20], 'z' => 0.05,
                                 'op' => 'view', 'os' => 'free', 'zm' => 'to_center',
                                 'adv' => {'nav_mode' => 'fly', 'fly_speed' => 1.0,
                                           'invert' => {'fly' => {}}})
    eye_move = camera.eye - before_eye
    target_move = camera.target - before_target
    assert('fly move translates eye and target') { changed && vector_length(eye_move) > EPSILON }
    assert('fly move preserves camera vector') { vector_length(eye_move - target_move) < EPSILON }

    # Fly forward inversion is independent of orbit and reverses translation.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    forward = camera.direction.clone
    before_eye = camera.eye.clone
    TrackballNav.apply('o' => [0, 0, 0], 'p' => [0, 0.10], 'z' => 0,
                       'adv' => {'nav_mode' => 'fly', 'fly_speed' => 1.0,
                                 'invert' => {'fly' => {}}})
    normal_forward = (camera.eye - before_eye).dot(forward)
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    before_eye = camera.eye.clone
    TrackballNav.apply('o' => [0, 0, 0], 'p' => [0, 0.10], 'z' => 0,
                       'adv' => {'nav_mode' => 'fly', 'fly_speed' => 1.0,
                                 'invert' => {'fly' => {'forward' => true}}})
    inverted_forward = (camera.eye - before_eye).dot(forward)
    assert('fly forward invert reverses movement') do
      normal_forward > EPSILON && inverted_forward < -EPSILON
    end

    # Walk look is horizon-locked; forward movement stays on XY while twist is ignored.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    before_eye = camera.eye.clone
    changed = TrackballNav.apply('o' => [0.08, 0.06, 0.50], 'p' => [0, 0], 'z' => 0,
                                 'adv' => {'nav_mode' => 'walk', 'walk_speed' => 1.0,
                                           'invert' => {'walk' => {}}})
    walk_right = camera.direction.cross(camera.up)
    assert('walk look turns in place') { changed && distance(before_eye, camera.eye) < EPSILON }
    assert('walk look keeps right axis horizontal') { walk_right.z.to_f.abs < EPSILON }
    before_eye = camera.eye.clone
    before_target = camera.target.clone
    changed = TrackballNav.apply('o' => [0, 0, 0], 'p' => [0, 0.15], 'z' => 0,
                                 'adv' => {'nav_mode' => 'walk', 'walk_speed' => 1.0,
                                           'invert' => {'walk' => {}}})
    eye_move = camera.eye - before_eye
    target_move = camera.target - before_target
    assert('walk forward stays on ground plane') do
      changed && eye_move.z.to_f.abs < EPSILON && vector_length(eye_move) > EPSILON
    end
    assert('walk move preserves camera vector') { vector_length(eye_move - target_move) < EPSILON }

    # Pan translates eye and target by exactly the same vector.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    before_eye = camera.eye.clone
    before_target = camera.target.clone
    changed = TrackballNav.apply('o' => [0, 0, 0], 'p' => [0.10, -0.05], 'z' => 0,
                                 'op' => 'view', 'os' => 'free', 'zm' => 'to_center')
    eye_move = camera.eye - before_eye
    target_move = camera.target - before_target
    assert('pan moves eye and target') { changed && vector_length(eye_move) > EPSILON }
    assert('pan preserves eye-target vector') { vector_length(eye_move - target_move) < EPSILON }

    # Perspective zoom is a dolly: eye-target distance changes without crossing target.
    set_camera(camera, [30, -30, 20], [0, 0, 0])
    before_distance = distance(camera.eye, camera.target)
    changed = TrackballNav.apply('o' => [0, 0, 0], 'p' => [0, 0], 'z' => 0.20,
                                 'op' => 'view', 'os' => 'free', 'zm' => 'to_center')
    after_distance = distance(camera.eye, camera.target)
    assert('perspective zoom changes eye-target distance') do
      changed && (after_distance - before_distance).abs > EPSILON && after_distance > 0.0
    end

    # Parallel zoom scales Camera#height.
    set_camera(camera, [30, -30, 20], [0, 0, 0], perspective: false, height: 50.0)
    before_height = camera.height.to_f
    changed = TrackballNav.apply('o' => [0, 0, 0], 'p' => [0, 0], 'z' => 0.20,
                                 'op' => 'view', 'os' => 'free', 'zm' => 'to_center')
    assert('parallel zoom changes camera height') do
      changed && (camera.height.to_f - before_height).abs > EPSILON
    end

    # Real screen-centre pickray/raytest, followed by a held view-pivot orbit.
    eye = box_center.offset(Geom::Vector3d.new(25, -35, 25))
    camera.perspective = true
    camera.set(eye, box_center, Geom::Vector3d.new(0, 0, 1))
    view.invalidate
    ray = view.pickray(view.vpwidth * 0.5, view.vpheight * 0.5)
    hit = model.raytest(ray)
    assert('pickray/raytest hits temporary box') { hit && hit[0].is_a?(Geom::Point3d) }
    surface_pivot = hit[0].clone
    before_pivot_distance = distance(camera.eye, surface_pivot)
    TrackballNav::CameraDriver.reset_gesture!
    changed = TrackballNav.apply('o' => [0.08, 0.04, 0], 'p' => [0, 0], 'z' => 0,
                                 'op' => 'view', 'os' => 'free', 'zm' => 'to_center')
    assert('view-pivot orbit applies') { changed }
    assert('held raycast pivot remains fixed') do
      (distance(camera.eye, surface_pivot) - before_pivot_distance).abs < EPSILON
    end

    report('PASS Trackball Nav SketchUp self-test complete')
  ensure
    model.abort_operation if operation_open
    camera.perspective = original[:perspective]
    camera.fov = original[:fov] if original[:perspective]
    camera.height = original[:height] unless original[:perspective]
    camera.set(original[:eye], original[:target], original[:up])
    view.invalidate
    TrackballNav.start if defined?(TrackballNav)
  end
end
