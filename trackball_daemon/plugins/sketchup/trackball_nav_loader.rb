# frozen_string_literal: true

require 'sketchup.rb'
require 'extensions.rb'

module TrackballNav
  ADDIN_VERSION = '0.2.13' unless const_defined?(:ADDIN_VERSION, false)

  unless file_loaded?(__FILE__)
    extension = SketchupExtension.new('Trackball Nav', 'trackball_nav/main')
    extension.description = 'Drives the SketchUp camera from the Trackball Daemon.'
    extension.version = ADDIN_VERSION
    extension.creator = 'Trackball Daemon'
    Sketchup.register_extension(extension, true)
    file_loaded(__FILE__)
  end
end
