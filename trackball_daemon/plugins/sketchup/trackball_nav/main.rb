# frozen_string_literal: true

require 'sketchup.rb'
require 'socket'
require 'json'

module TrackballNav
  ADDIN_VERSION = '0.2.4' unless const_defined?(:ADDIN_VERSION, false)
  DEFAULT_PORT = 47_900 unless const_defined?(:DEFAULT_PORT, false)
  TIMER_INTERVAL = 0.02 unless const_defined?(:TIMER_INTERVAL, false)
  RETRY_INTERVAL = 1.5 unless const_defined?(:RETRY_INTERVAL, false)
  MAX_FRAMES_PER_TICK = 64 unless const_defined?(:MAX_FRAMES_PER_TICK, false)

  @timer = nil
  @socket = nil
  @connecting = false
  @rx_buffer = +''
  @tx_buffer = +''
  @next_retry = 0.0
  @rate_limits = {}

  class << self
    def start
      stop
      @next_retry = 0.0
      @timer = UI.start_timer(TIMER_INTERVAL, true) { pump }
      log("start: Trackball Nav v#{ADDIN_VERSION} (SketchUp #{Sketchup.version}, Ruby #{RUBY_VERSION})")
      true
    rescue StandardError => error
      log_error('start', error)
      false
    end

    def stop
      if @timer
        UI.stop_timer(@timer)
        @timer = nil
      end
      disconnect
      true
    rescue StandardError
      false
    end

    def apply(frame)
      CameraDriver.apply(frame)
    end

    def pump
      now = Time.now.to_f
      begin_connect(now) unless @socket || now < @next_retry
      finish_connect if @socket && @connecting
      return unless @socket && !@connecting

      flush_hello
      read_frames if @tx_buffer.empty?
    rescue EOFError, IOError, SystemCallError, SocketError => error
      log_rate_limited('socket', "socket: #{error.class}: #{error.message}")
      schedule_reconnect
    rescue StandardError => error
      log_error('pump', error)
    end

    def log(message)
      appdata = ENV.fetch('APPDATA', '')
      directory = File.join(appdata, 'TrackballDaemon')
      Dir.mkdir(directory) unless Dir.exist?(directory)
      File.open(File.join(directory, 'sketchup_addin.log'), 'a:utf-8') do |file|
        file.puts("#{Time.now.strftime('%H:%M:%S')} #{message}")
      end
    rescue StandardError
      nil
    end

    def log_error(context, error)
      trace = Array(error.backtrace).first(8).join(' | ')
      log_rate_limited("error-#{context}", "#{context} error: #{error.class}: #{error.message} | #{trace}", 5.0)
    end

    def log_rate_limited(key, message, period = 2.0)
      now = Time.now.to_f
      return if now - @rate_limits.fetch(key, 0.0) < period

      @rate_limits[key] = now
      log(message)
    end

    private

    def begin_connect(now)
      port = bridge_port
      @socket = Socket.new(Socket::AF_INET, Socket::SOCK_STREAM, 0)
      @socket.setsockopt(Socket::IPPROTO_TCP, Socket::TCP_NODELAY, 1)
      @connecting = false
      @rx_buffer = +''
      @tx_buffer = +''
      begin
        @socket.connect_nonblock(Socket.sockaddr_in(port, '127.0.0.1'))
        connected!
      rescue IO::WaitWritable
        @connecting = true
      rescue Errno::EISCONN
        connected!
      end
    rescue SystemCallError, SocketError => error
      log_rate_limited('connect', "connect: #{error.class}: #{error.message}")
      schedule_reconnect(now)
    end

    def finish_connect
      return unless IO.select(nil, [@socket], nil, 0)

      error_number = @socket.getsockopt(Socket::SOL_SOCKET, Socket::SO_ERROR).int
      raise SystemCallError.new('connect', error_number) unless error_number.zero?

      connected!
    end

    def connected!
      @connecting = false
      @tx_buffer = JSON.generate(
        'type' => 'hello',
        'app' => 'sketchup',
        'version' => ADDIN_VERSION,
        'host' => Sketchup.version,
        'pid' => Process.pid
      ) + "\n"
      log("connected: broker port=#{bridge_port}")
    end

    def flush_hello
      return if @tx_buffer.empty?

      written = @socket.write_nonblock(@tx_buffer)
      @tx_buffer = @tx_buffer.byteslice(written..-1) || +''
    rescue IO::WaitWritable
      nil
    end

    def read_frames
      loop do
        begin
          @rx_buffer << @socket.read_nonblock(4096)
        rescue IO::WaitReadable
          break
        end
      end

      applied = 0
      while applied < MAX_FRAMES_PER_TICK && (newline = @rx_buffer.index("\n"))
        line = @rx_buffer.slice!(0, newline + 1).strip
        next if line.empty?

        begin
          frame = JSON.parse(line)
          apply(frame)
          applied += 1
        rescue JSON::ParserError => error
          log_rate_limited('json', "bad broker JSON: #{error.message}")
        end
      end
    end

    def schedule_reconnect(now = Time.now.to_f)
      disconnect
      @next_retry = now + RETRY_INTERVAL
    end

    def disconnect
      @socket.close if @socket && !@socket.closed?
    rescue StandardError
      nil
    ensure
      @socket = nil
      @connecting = false
      @rx_buffer = +''
      @tx_buffer = +''
    end

    def bridge_port
      path = File.join(ENV.fetch('APPDATA', ''), 'TrackballDaemon', 'bridge.json')
      data = JSON.parse(File.read(path, encoding: 'UTF-8'))
      Integer(data.fetch('port', DEFAULT_PORT))
    rescue StandardError
      DEFAULT_PORT
    end
  end
end

Sketchup.require(File.join(__dir__, 'camera'))
Sketchup.require(File.join(__dir__, 'cursor'))
TrackballNav::CursorTracker.install     # wires CameraDriver.cursor_refresh (under-mouse pivot; no-op off-Windows)
TrackballNav.start
