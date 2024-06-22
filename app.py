import os, io, logging, json, time, re
from datetime import datetime
from threading import Condition
import threading


from flask import Flask, render_template, request, jsonify, Response, send_file, abort

from PIL import Image

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.encoders import MJPEGEncoder
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput
from picamera2.outputs import FileOutput
from libcamera import Transform, controls

# Flask Application
app = Flask(__name__)

# picamera2
picam2 = Picamera2()

# Vi tri thu muc cua file
current_dir = os.path.dirname(os.path.abspath(__file__))

# Duong dan den camera-config.json
camera_config_path = os.path.join(current_dir, 'camera-config.json')

# Lay setting tu camera-config.json
with open(camera_config_path, "r") as file:
	camera_config = json.load(file)
print(f'\nCamera config:\n{camera_config}\n')

# Trich xuat cac thong tin
live_settings = camera_config.get('controls', {})
rotation_settings = camera_config.get('rotation', {})
sensor_mode = camera_config.get('sensor-mode', 1)
capture_settings = camera_config.get('capture-settings', {})

selected_resolution = capture_settings["Resolution"]
resolution = capture_settings["available-resolutions"][selected_resolution]
print(f'\nCamera settings:\n{capture_settings}\n')
print(f'\nCamera set resolution:\n{resolution}\n')

# Lay sensor_mode cho camera
camera_modes = picam2.sensor_modes
mode = picam2.sensor_modes[sensor_mode]

# Tao video_config
video_config = picam2.create_video_configuration(main={'size':resolution}, sensor={'output_size':mode['size'], 'bit_depth':mode['bit_depth']})
print(f'\nVideo config:\n{video_config}\n')

default_settings = picam2.camera_controls
live_settings = {key: value for key,value in live_settings.items() if key in default_settings}

# Duong dan den camera-module-info.json
camera_module_info_path = os.path.join(current_dir, 'camera-module-info.json')

# Trich xuat cac thong tin
with open(camera_module_info_path, "r") as file:
	camera_module_info = json.load(file)
camera_properties = picam2.camera_properties
print(f'\nPicamera2 camera properties:\n{camera_properties}\n')

# Folder luu tru anh
UPLOAD_FOLDER = os.path.join(current_dir, 'static/gallery')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Tao folder neu chua co
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Folder luu tru video
UPLOAD_FOLDER_VIDEOS = os.path.join(current_dir, 'static/videos')
app.config['UPLOAD_FOLDER_VIDEOS'] = UPLOAD_FOLDER_VIDEOS

# Tao folder neu chua co
os.makedirs(app.config['UPLOAD_FOLDER_VIDEOS'], exist_ok=True)

######################################################################################################
# Streaming Class
output = None
class StreamingOutput(io.BufferedIOBase):
	def __init__(self):
		self.frame = None
		self.condition = Condition()

	def write(self, buf):
		with self.condition:
			self.frame = buf
			self.condition.notify_all()

# Ham tao cac frame anh
def generate():
	global output
	while True:
		with output.condition:
			output.condition.wait()
			frame = output.frame
		yield (b'--frame\r\n'
		       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

#######################################################################################################
# Ham lay config tu file
def load_settings(settings_file):
	try:
		with open(settings_file, 'r') as file:
			settings = json.load(file)
			print(settings)
	except FileNotFoundError:
		logging.error(f"Khong tim thay file settings {settings_file}!")
		return None
	except Exception as e:
		logging.error(f"Loi khi load camera settings: {e}")
		return None

########################################################################################################
# Routes den cac page cua website
@app.route("/")
def home():
	return render_template("camerasettings.html", title="Smart Doorbell", live_settings=live_settings, rotation_settings=rotation_settings, settings_from_camera=default_settings, capture_settings=capture_settings)

# Route den page thong tin ve camera
@app.route("/camera_info")
def camera_info():
	connected_camera = picam2.camera_properties['Model']
	connected_camera_data = next((module for module in camera_module_info["camera_modules"] if module["sensor_model"] == connected_camera), None)
	if connected_camera_data:
		return render_template("camera_info.html", title="Camera info", connected_camera_data = connected_camera_data, camera_modes = camera_modes, sensor_mode = sensor_mode)
	else:
		return jsonify(error = "Khong tim thay Module camera")

# Video feed de stream video len web
@app.route("/video_feed")
def video_feed():
	return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


########################################################################################################
# Route de luu setting vao buffer

# Cap nhap setting cua do phan giai video
@app.route('/update_live_settings', methods=['POST'])
def update_settings():
	global picam2, capture_settings, resolution, sensor_mode, mode, live_settings, video_config
	try:
		data = request.get_json()
		print(data)
		for key in data:
			if key in ('Resolution'):
				capture_settings['Resolution'] = int(data[key])
				selected_resolution = int(data[key])
				resolution = capture_settings["available-resolutions"][selected_resolution]
				print(resolution)
				stop_camera_stream()
				video_config = picam2.create_video_configuration(main={'size':resolution}, sensor={'output_size': mode['size'], 'bit_depth': mode['bit_depth']})
				print(f'\nVideo config:\n{video_config}\n')
				start_camera_stream()
				return jsonify(success = True, message = "Update setting thanh cong!", settings = capture_settings)
	except Exception as e:
		return jsonify(success = False, message = str(e))

# Cap nhap setting cua quay huong man hinh video
@app.route('/update_restart_settings', methods = ['POST'])
def update_restart_settings():
	global rotation_settings, video_config
	try:
		data = request.get_json()
		stop_camera_stream()
		transform = Transform()
		for key, value in data.items():
			if key in rotation_settings:
				rotation_settings[key] = data[key]
				setattr(transform, key, value)
			video_config["transform"] = transform
		start_camera_stream()
		return jsonify(success = True, message = "Update restart setting thanh cong!", settings = live_settings)
	except Exception as e:
		return jsonify(success = False, message = str(e))

# Reset setting mac dinh cua camera
@app.route('/reset_default_live_settings', methods = ["GET"])
def reset_default_live_settings():
	global rotation_settings
	try:
		default_settings = picam2.camera_controls
		for key, value in rotation_settings.items():
			rotation_settings[key] = 2
		restart_configure_camera(rotation_settings)
		return jsonify(data1 = live_settings, data2 = rotation_settings)
	except Exception as e:
		return jsonify(error = str(e))

# Luu settings da dieu chinh
@app.route('/save_settings', methods = ['GET'])
def save_settings():
	global rotation_settings, capture_settings, video_config
	try:
		with open('camera-config.json', 'r') as file:
			camera_config = json.load(file)
			
		for key, value in rotation_settings.items():
			if key in camera_config['rotation']:
				camera_config['rotation'][key] = value
		
		for key, value in capture_settings.items():
			if key in camera_config['capture-settings']:
				camera_config['capture-settings'][key] = value
				
		with open('camera-config.json', 'w') as file:
			json.dump(camera_config, file, indent=4)
		
		return jsonify(success = True, message = "Luu thiet lap thanh cong!")
	except Exception as e:
		logging.error(f"Loi khi luu thiet lap: {e}")
		return jsonify(success = False, message = str(e))
		
def save_sensor_mode(sensor_mode):
	try:
		with open('camera-config.json', 'r') as file:
			camera_config = json.load(file)
		
		camera_config['sensor-mode'] = sensor_mode
		
		with open('camera-config.json', 'w') as file:
			json.dump(camera_config, file, indent=4)
			
		return jsonify(success = True, message = "Luu thiet lap thanh cong!")
	
	except Exception as e:
		logging.error(f"Loi khi luu thiet lap: {e}")
		return jsonify(success = False, message = (e))
		
########################################################################################################
# Chup anh va quay video 10s

# Chup anh
@app.route('/capture_photo', methods = ["POST"])
def capture_photo():
	try:
		take_photo()
		time.sleep(1)
		return jsonify(success = True, message = "Anh da duoc chup")
	except Exception as e: 
		return jsonify(success = False, message = (e))

def take_photo():
	global picam2, capture_settings
	try:
		timestamp = int(datetime.timestamp(datetime.now()))
		image_name = f'pimage_{timestamp}'
		filepath = os.path.join(app.config['UPLOAD_FOLDER'], image_name)
		request = picam2.capture_request()
		request.save("main", f'{filepath}.jpg')
		request.release()
		logging.info(f"Chup anh thanh cong! Duong dan: {filepath}")
	except Exception as e:
		logging.error(f"Loi chup anh: {e}")
		
# Quay video 10s
@app.route('/record_video', methods=["POST"])
def record_video():
    try:
        record_10s_video()
        return jsonify(success=True, message= "Video da duoc ghi")
    except Exception as e:
        return jsonify(success=False, message=str(e))
		
def record_10s_video():
    try:
        timestamp = int(datetime.timestamp(datetime.now()))
        encoder = H264Encoder()
        filename = f'video_{timestamp}.mp4'
        video_path = os.path.join(app.config['UPLOAD_FOLDER_VIDEOS'], filename)
        print(video_path)
        output = FfmpegOutput(video_path)
        
        # Start recording
        picam2.start_recording(encoder, output)
        time.sleep(10)  # Record for 10 seconds
        picam2.stop_recording()
        start_camera_stream()

    except Exception as e:
        logging.error(f"Error recording video: {e}")

########################################################################################################
# Start va Stop stream video
def start_camera_stream():
	global picam2, output, video_config
	picam2.configure(video_config)
	output = StreamingOutput()
	picam2.start_recording(JpegEncoder(), FileOutput(output))
	metadata = picam2.capture_metadata()
	time.sleep(1)

def stop_camera_stream():
	global picam2
	picam2.stop_recording()
	time.sleep(1)
	
#########################################################################################################
# Configure camera
def configure_camera(live_settings):
	picam2.set_controls(live_settings)
	time.sleep(0.5)

#########################################################################################################
# Thu vien anh
@app.route('/image_gallery')
def image_gallery():
	try:
		image_files = {f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.jpg')}
		print(image_files)
		
		if not image_files:
			return render_template('no_files.html')
		
		# Tao mot danh sach chua ten file, timestamp
		files_and_timestamps = []
		for image_file in image_files:
			# Lay unix_timestamp tu ten file
			unix_timestamp = int(image_file.split('_')[-1].split('.')[0])
			timestamp = datetime.utcfromtimestamp(unix_timestamp).strftime('%d-%m-%Y %H:%M:%S')
			# Then vao danh sach
			files_and_timestamps.append({'filename': image_file, 'timestamp': timestamp})
		
		# Sap xep dua theo unix_timestamp
		files_and_timestamps.sort(key = lambda x: x['timestamp'], reverse = True)
		
		# Pagination
		page = request.args.get('page', 1, type=int)
		items_per_page = 15
		total_pages = (len(files_and_timestamps) + items_per_page - 1) // items_per_page
		
		# Tinh so thu tu cua dau vao cuoi trang cho Pagination
		start_page = max(1, page - 1)
		end_page = min(page+3, total_pages)
		start_index = (page - 1) * items_per_page
		end_index = start_index + items_per_page
		files_and_timestamps_page = files_and_timestamps[start_index:end_index]
		
		return render_template('image_gallery.html', image_files = files_and_timestamps_page, page = page, start_page = start_page, end_page = end_page)
	except Exception as e:
		logging.error(f"Khong load duoc thu vien anh: {e}")
		return render_template('error.html', error = str(e))
		
# Xem, tai ve va xoa anh
@app.route('/view_image/<filename>', methods = ['GET'])
def view_image(filename):
	return render_template('view_image.html', filename = filename)

@app.route('/download_image/<filename>/', methods = ['GET'])
def download_image(filename):
	try:
		image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
		return send_file(image_path, as_attachment = True)
	except Exception as e:
		print(f"Loi khi tai anh: {e}")
		abort(500)
		
@app.route('/delete_image/<filename>', methods = ["DELETE"])
def delete_image(filename):
	try:
		filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
		os.remove(filepath)
		return jsonify(success = True, message = "Xoa anh thanh cong!")
	except Exception as e:
		return jsonify(success = False, message = str(e))

# Thu vien video
@app.route('/video_gallery')
def video_gallery():
    try:
        video_files = [f for f in os.listdir(app.config['UPLOAD_FOLDER_VIDEOS']) if f.endswith('.mp4')]
        if not video_files:
            return render_template('no_videos.html')
        
        # Create a list of video file names and timestamps
        videos_and_timestamps = []
        for video_file in video_files:
            unix_timestamp = int(video_file.split('_')[-1].split('.')[0])
            timestamp = datetime.utcfromtimestamp(unix_timestamp).strftime('%d-%m-%Y %H:%M:%S')
            videos_and_timestamps.append({'filename': video_file, 'timestamp': timestamp})
        
        # Sort by timestamp in descending order
        videos_and_timestamps.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # Pagination
        page = request.args.get('page', 1, type=int)
        items_per_page = 15
        total_pages = (len(videos_and_timestamps) + items_per_page - 1) // items_per_page
        
        # Calculate pagination bounds
        start_page = max(1, page - 1)
        end_page = min(page + 3, total_pages)
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        videos_and_timestamps_page = videos_and_timestamps[start_index:end_index]
        
        return render_template('video_gallery.html', video_files=videos_and_timestamps_page, page=page, start_page=start_page, end_page=end_page)
    except Exception as e:
        logging.error(f"Failed to load video gallery: {e}")
        return render_template('error.html', error=str(e))

# Xem video
@app.route('/view_video/<filename>', methods=['GET'])
def view_video(filename):
    try:
        return render_template('view_video.html', filename=filename)
    except Exception as e:
        return render_template('error.html', error=str(e))

# Download video
@app.route('/download_video/<filename>', methods=['GET'])
def download_video(filename):
    try:
        video_path = os.path.join(app.config['UPLOAD_FOLDER_VIDEOS'], filename)
        return send_file(video_path, as_attachment=True)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

# Xoa video
@app.route('/delete_video/<filename>', methods=["DELETE"])
def delete_video(filename):
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER_VIDEOS'], filename)
        os.remove(filepath)
        return jsonify(success=True, message="Xoa video thanh cong!")
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


#########################################################################################################
# Chay app
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Start Camera stream
    start_camera_stream()

    # Load and set camera settings
    configure_camera(live_settings)

    # Start the Flask application
    app.run(debug=False, host='0.0.0.0', port=8080)
