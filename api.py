from flask import Flask, request, jsonify, Response
from datetime import datetime, timedelta
import pandas as pd
import os
import io
import csv
import shutil
from insightface_detector import InsightFaceDetector
from insightface_utils import process_image
import faiss
import numpy as np
from flask_cors import CORS
from flask import Flask, request, jsonify
from gtts import gTTS
from config import config

app = Flask(__name__)
CORS(app)
detector = InsightFaceDetector()

# Kết nối tới MongoDB
users_collection = config.users_collection
managers_collection = config.managers_collection
camera_collection = config.camera_collection
data_collection = config.data_collection
save_path = config.save_path

# ----------------------------------------------------------------
def process_user_photos():
    users = users_collection.find()

    for user in users:
        user_id = user["_id"]
        photo_folder = user["photo_folder"]

        print(f"Processing user ID {user_id}...")

        if not os.path.exists(photo_folder):
            print(f"Photo folder does not exist for user ID {user_id}: {photo_folder}")
            continue

        face_embeddings = []
        photo_count = 0
        for file_name in os.listdir(photo_folder):
            file_path = os.path.join(photo_folder, file_name)

            if os.path.isfile(file_path):
                try:
                    # Gọi hàm xử lý để lấy face embedding
                    face_embedding = process_image(file_path, detector)
                    
                    # Chuyển face embedding sang dạng danh sách
                    face_embeddings.append(face_embedding.tolist())

                    photo_count += 1
                    print(f"Added embedding for file {file_name} (user ID {user_id})")

                except Exception as e:
                    print(f"Error processing file {file_path}: {e}")

        # Cập nhật face_embeddings vào users_collection
        users_collection.update_one(
            {"_id": user_id},
            {"$set": {"face_embeddings": face_embeddings}}
        )

        print(f"Completed processing user ID {user_id}. Total photos processed: {photo_count}")
        print("-" * 80)

# ----------------------------------------------------------------
def update_all_faiss_index(output_path=save_path + "/data_base/face_index.faiss"):
    embeddings = []
    ids = []

    # Lấy tất cả người dùng từ MongoDB
    users = list(users_collection.find())
    for user in users:
        user_id = user["_id"]  # ID của người dùng
        face_embeddings = user.get("face_embeddings", [])  # Lấy danh sách face_embeddings

        # Duyệt qua tất cả face_embeddings và thêm vào danh sách
        for embedding in face_embeddings:
            embeddings.append(embedding)  # Thêm embedding vào danh sách
            ids.append(user_id)  # Dùng MongoDB `_id` làm ID

    if len(embeddings) == 0:
        print("No embeddings found in the database.")
        return

    # Chuyển embeddings thành numpy array
    embeddings_array = np.array(embeddings, dtype="float32")
    ids_array = np.array(ids, dtype=np.int64)  # FAISS yêu cầu ID là kiểu số nguyên

    # Tạo FAISS index với Inner Product (cho Cosine Similarity)
    dimension = embeddings_array.shape[1]
    index = faiss.IndexFlatIP(dimension)  # Inner Product để tính Cosine Similarity
    index_with_id = faiss.IndexIDMap(index)

    # Thêm embeddings và IDs vào FAISS
    index_with_id.add_with_ids(embeddings_array, ids_array)

    # Ghi FAISS index ra tệp
    faiss.write_index(index_with_id, output_path)
    print(f"FAISS index (Cosine Similarity) saved to {output_path}")

# ----------------------------------------------------------------
def update_faiss_index(new_embedding, user_id, index_path=save_path + "/data_base/face_index.faiss"):
    # Kiểm tra nếu tệp FAISS index đã tồn tại
    if os.path.exists(index_path):
        # Tải FAISS index hiện tại
        index = faiss.read_index(index_path)
    else:
        # Tạo FAISS index mới nếu chưa tồn tại
        dimension = len(new_embedding)
        index = faiss.IndexIDMap(faiss.IndexFlatIP(dimension))

    # Chuyển đổi embedding và ID sang numpy array
    embedding_array = np.array([new_embedding], dtype="float32")
    id_array = np.array([user_id], dtype=np.int64)

    # Thêm embedding mới vào FAISS index
    index.add_with_ids(embedding_array, id_array)

    # Lưu FAISS index vào tệp
    faiss.write_index(index, index_path)
    print(f"Updated FAISS index saved to {index_path}")

# ----------------------------------------------------------------
@app.route('/api/get_all_users', methods=['GET'])
def get_all_users():
    users = list(users_collection.find())
    return jsonify(users)

# ----------------------------------------------------------------
@app.route('/api/add_user', methods=['POST'])
def add_user():
    data = request.json
    required_fields = ["full_name", "department_id"]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    # Tạo ID tự động tăng dần
    last_user = users_collection.find_one(sort=[("_id", -1)])
    user_id = last_user["_id"] + 1 if last_user else 1

    # Tạo đường dẫn thư mục cho user
    folder_path = f"{save_path}/uploads/user_{user_id}"
    
    # Chuẩn bị dữ liệu để thêm vào MongoDB
    user = {
        "_id": user_id,
        "full_name": data["full_name"],
        "department_id": data["department_id"],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "photo_folder": folder_path
    }

    try:
        # Thêm user vào MongoDB
        users_collection.insert_one(user)

        # Tạo thư mục cho user
        os.makedirs(folder_path, exist_ok=True)

        return jsonify({"message": "User added", "id": user_id, "photo_folder": folder_path}), 201
    except Exception as e:
        return jsonify({"error": f"Failed to add user: {str(e)}"}), 500

# ----------------------------------------------------------------
@app.route('/api/delete_user/<user_id>', methods=['DELETE'])
def delete_user(user_id):
    try:
        # Lấy thông tin user từ MongoDB
        user = users_collection.find_one({"_id": int(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Xóa user khỏi MongoDB
        result = users_collection.delete_one({"_id": int(user_id)})
        if result.deleted_count == 0:
            return jsonify({"error": "Failed to delete user"}), 500

        # Xóa thư mục của user
        folder_path = user.get("photo_folder")
        if folder_path and os.path.exists(folder_path):
            shutil.rmtree(folder_path)  # Xóa toàn bộ thư mục và nội dung bên trong

        return jsonify({"message": "User and folder deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete user: {str(e)}"}), 500

# ----------------------------------------------------------------
@app.route('/api/upload_photo/<user_id>', methods=['POST'])
def upload_photo(user_id):
    if 'photo' not in request.files:
        return jsonify({"error": "No photo uploaded"}), 400

    photo = request.files['photo']

    # Lấy thông tin user từ MongoDB
    user = users_collection.find_one({"_id": int(user_id)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Đường dẫn thư mục của user
    folder_path = user.get("photo_folder")
    if not folder_path:
        return jsonify({"error": "User folder not found"}), 500

    try:
        # Đảm bảo thư mục tồn tại
        os.makedirs(folder_path, exist_ok=True)

        # Lưu ảnh vào thư mục
        filename = f"{photo.filename}"
        file_path = os.path.join(folder_path, filename)
        photo.save(file_path)

        # Lấy đặc trưng khuôn mặt từ hàm `process_image`
        try:
            face_embedding = process_image(file_path, detector)

            if face_embedding is not None:
                # Lưu đặc trưng khuôn mặt vào MongoDB
                face_embedding = face_embedding.tolist()

                users_collection.update_one(
                    {"_id": int(user_id)},
                    {"$push": {"face_embeddings": face_embedding}}
                )

                # Cập nhật FAISS index với embedding mới
                update_faiss_index(face_embedding, int(user_id))

                return jsonify({"message": "Photo uploaded and face features saved"}), 200
            else:
                return jsonify({"error": "No face detected in the image"}), 400

        except Exception as e:
            return jsonify({"error": f"Failed to process image: {str(e)}"}), 500

    except Exception as e:
        return jsonify({"error": f"Failed to upload photo: {str(e)}"}), 500
    
# ----------------------------------------------------------------
# @app.route('/delete_image', methods=['DELETE'])
# def delete_image():
#     data = request.json

#     # Kiểm tra đầu vào
#     required_fields = ["username", "password", "image_path"]
#     if not all(field in data for field in required_fields):
#         return jsonify({"error": "Missing required fields"}), 400

#     username = data["username"]
#     password = data["password"]
#     image_name = data["image_path"]  # Tên ảnh

#     # Kiểm tra user có tồn tại và mật khẩu đúng không
#     user = users_collection.find_one({"username": username, "password": password})
#     if not user:
#         return jsonify({"error": "Invalid username or password"}), 401

#     user_id = user["_id"]
#     user_dir = f"data_set/{user_id}"
#     image_path = os.path.join(user_dir, image_name)  # Tạo đường dẫn đầy đủ

#     # Kiểm tra xem ảnh có tồn tại trong MongoDB không
#     image_record = users_collection.find_one({"_id": user_id, "images.path": image_path})
#     if not image_record:
#         return jsonify({"error": "Image not found in the database"}), 404

#     # Xóa thông tin ảnh trong MongoDB
#     users_collection.update_one(
#         {"_id": user_id},
#         {"$pull": {"images": {"path": image_path}}}
#     )

#     # Xóa ảnh vật lý trong thư mục
#     try:
#         if os.path.exists(image_path):
#             os.remove(image_path)
#             # Gọi các hàm chỉ khi việc xoá ảnh thành công
#             try:
#                 update_embeddings_for_all_users(detector=detector)
#                 create_faiss_index_with_mongo_id_cosine()
#             except Exception as e:
#                 return jsonify({"error": f"Failed to update embeddings or FAISS index: {str(e)}"}), 500
#             return jsonify({"message": "Image deleted successfully"}), 200
#         else:
#             return jsonify({"error": "Image file not found in the directory"}), 404
#     except Exception as e:
#         return jsonify({"error": f"Failed to delete image: {str(e)}"}), 500

# ----------------------------------------------------------------
@app.route('/api/get_all_managers', methods=['GET'])
def get_all_managers():
    managers = list(managers_collection.find({}, {"_id": 1, "fullname": 1, "department_id": 1, "username": 1}))
    for manager in managers:
        manager["_id"] = str(manager["_id"])
    return jsonify(managers), 200

# ----------------------------------------------------------------
@app.route('/api/add_manager', methods=['POST'])
def add_manager():
    data = request.json

    # Kiểm tra dữ liệu đầu vào
    required_fields = ["fullname", "department_id", "username", "password"]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    # Kiểm tra username đã tồn tại chưa
    existing_manager = managers_collection.find_one({"username": data["username"]})
    if existing_manager:
        return jsonify({"error": "Username already exists"}), 409  # 409: Conflict

    # Tạo ID tự động tăng dần
    last_manager = managers_collection.find_one(sort=[("_id", -1)])
    manager_id = last_manager["_id"] + 1 if last_manager else 1

    # Thêm manager vào MongoDB
    manager = {
        "_id": manager_id,
        "fullname": data["fullname"],
        "department_id": data["department_id"],
        "username": data["username"],
        "password": data["password"],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        managers_collection.insert_one(manager)
        return jsonify({"message": "Manager added", "id": manager_id}), 201
    except Exception as e:
        return jsonify({"error": f"Failed to add manager: {str(e)}"}), 500

# ----------------------------------------------------------------
@app.route('/api/delete_manager/<manager_id>', methods=['DELETE'])
def delete_manager(manager_id):
    result = managers_collection.delete_one({"_id": int(manager_id)})
    if result.deleted_count == 0:
        return jsonify({"error": "Manager not found"}), 404
    return jsonify({"message": "Manager deleted"}), 200

# ----------------------------------------------------------------
@app.route('/add_camera', methods=['POST'])
def add_camera():
    data = request.json

    # Kiểm tra dữ liệu đầu vào
    required_fields = ["id_camera", "camera_name", "camera_address", "camera_location"]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    id_camera = data["id_camera"]
    camera_name = data["camera_name"]
    camera_address = data["camera_address"]
    camera_location = data["camera_location"]

    # Kiểm tra id_camera có bị trùng không
    existing_camera = camera_collection.find_one({"id_camera": id_camera})
    if existing_camera:
        return jsonify({"error": "Camera ID already exists"}), 400

    # Tạo ID tự động tăng dần cho camera
    last_camera = camera_collection.find_one(sort=[("_id", -1)])
    camera_id = last_camera["_id"] + 1 if last_camera else 1

    # Chuẩn bị dữ liệu để thêm vào MongoDB
    camera_info = {
        "_id": camera_id,
        "id_camera": id_camera,
        "camera_name": camera_name,
        "camera_address": camera_address,
        "camera_location": camera_location,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    # Thêm camera vào collection
    camera_collection.insert_one(camera_info)
    return jsonify({
        "message": "Camera added successfully",
        "id": camera_id,
        "id_camera": id_camera,
        "camera_name": camera_name,
        "camera_address": camera_address,
        "camera_location": camera_location
    }), 201

# ----------------------------------------------------------------
@app.route('/emotion_summary', methods=['POST'])
def emotion_summary():
    data = request.json

    # Kiểm tra đầu vào
    required_fields = ["timestamp_start", "timestamp_end", "id", "camera_name"]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        timestamp_start = datetime.strptime(data["timestamp_start"], "%Y-%m-%d %H:%M:%S")
        timestamp_end = datetime.strptime(data["timestamp_end"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return jsonify({"error": "Invalid timestamp format, use 'YYYY-MM-DD HH:MM:SS'"}), 400

    user_id = data["id"]
    camera_name = data["camera_name"]

    # Lọc dữ liệu theo id, camera_name, và khoảng thời gian
    tracking_data = list(data_collection.find({
        "id": user_id,
        "camera_name": camera_name,
        "timestamp": {"$gte": timestamp_start.strftime("%Y-%m-%d %H:%M:%S"),
                      "$lte": timestamp_end.strftime("%Y-%m-%d %H:%M:%S")}
    }))

    if not tracking_data:
        return jsonify({"error": "No tracking data found for the given criteria"}), 404

    # Tìm thông tin user
    user_info = users_collection.find_one({"_id": user_id})
    if not user_info:
        return jsonify({"error": f"User with ID {user_id} not found"}), 404

    # Tính tổng cảm xúc
    emotion_count = {}
    total_emotions = 0
    for record in tracking_data:
        emotion = record["emotion"]
        emotion_prob = record["emotion_prob"]

        if emotion in emotion_count:
            emotion_count[emotion] += emotion_prob
        else:
            emotion_count[emotion] = emotion_prob

        total_emotions += emotion_prob

    if total_emotions == 0:
        return jsonify({"error": "No emotions recorded in the tracking data"}), 404

    # Tính phần trăm từng cảm xúc
    emotion_percentages = {
        emotion: round((prob / total_emotions) * 100, 2)
        for emotion, prob in emotion_count.items()
    }

    # Chuẩn bị dữ liệu trả về
    result = {
        "timestamp_start": timestamp_start.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_end": timestamp_end.strftime("%Y-%m-%d %H:%M:%S"),
        "id": user_id,
        "full_name": user_info["full_name"],
        "department_id": user_info["department_id"],
        "emotion_percentages": emotion_percentages
    }

    return jsonify(result), 200

# ----------------------------------------------------------------
@app.route('/get_tracking_data', methods=['POST'])
def get_tracking_data():
    data = request.json

    # Kiểm tra đầu vào
    required_fields = ["timestamp_start", "timestamp_end", "id", "camera_name"]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        timestamp_start = datetime.strptime(data["timestamp_start"], "%Y-%m-%d %H:%M:%S")
        timestamp_end = datetime.strptime(data["timestamp_end"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return jsonify({"error": "Invalid timestamp format, use 'YYYY-MM-DD HH:MM:SS'"}), 400

    user_id = data["id"]
    camera_name = data["camera_name"]

    # Lọc dữ liệu trong khoảng thời gian và camera_name
    tracking_data = list(data_collection.find({
        "id": user_id,
        "camera_name": camera_name,
        "timestamp": {"$gte": timestamp_start.strftime("%Y-%m-%d %H:%M:%S"),
                      "$lte": timestamp_end.strftime("%Y-%m-%d %H:%M:%S")}
    }))

    if not tracking_data:
        return jsonify({"error": "No tracking data found for the given criteria"}), 404

    # Tìm thông tin user
    user_info = users_collection.find_one({"_id": user_id})
    if not user_info:
        return jsonify({"error": f"User with ID {user_id} not found"}), 404

    # Chuyển đổi ObjectId thành chuỗi
    for record in tracking_data:
        record["_id"] = str(record["_id"])  # Chuyển ObjectId thành chuỗi

    # Chuẩn bị kết quả trả về
    result = {
        "timestamp_start": timestamp_start.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_end": timestamp_end.strftime("%Y-%m-%d %H:%M:%S"),
        "id": user_id,
        "full_name": user_info["full_name"],
        "department_id": user_info["department_id"],
        "tracking_data": tracking_data
    }
    return jsonify(result), 200

# ----------------------------------------------------------------
@app.route('/get_tracking_data_csv', methods=['POST'])
def get_tracking_data_csv():
    data = request.json

    # Kiểm tra đầu vào
    required_fields = ["timestamp_start", "timestamp_end", "id", "camera_name"]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        timestamp_start = datetime.strptime(data["timestamp_start"], "%Y-%m-%d %H:%M:%S")
        timestamp_end = datetime.strptime(data["timestamp_end"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return jsonify({"error": "Invalid timestamp format, use 'YYYY-MM-DD HH:MM:SS'"}), 400

    user_id = data["id"]
    camera_name = data["camera_name"]

    # Lọc dữ liệu trong khoảng thời gian và camera_name
    tracking_data = list(data_collection.find({
        "id": user_id,
        "camera_name": camera_name,
        "timestamp": {"$gte": timestamp_start.strftime("%Y-%m-%d %H:%M:%S"),
                      "$lte": timestamp_end.strftime("%Y-%m-%d %H:%M:%S")}
    }))

    if not tracking_data:
        return jsonify({"error": "No tracking data found for the given criteria"}), 404

    # Chuyển đổi ObjectId thành chuỗi
    for record in tracking_data:
        record["_id"] = str(record["_id"])

    # Tạo CSV trong bộ nhớ
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=tracking_data[0].keys())
    writer.writeheader()
    writer.writerows(tracking_data)

    # Trả về CSV dưới dạng response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=tracking_data.csv"}
    )

# ----------------------------------------------------------------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json  # Dữ liệu từ client gửi lên
    username = data.get('username')
    password = data.get('password')

    # Tìm manager trong MongoDB
    manager = managers_collection.find_one({"username": username})
    if not manager:
        return jsonify({"success": False, "message": "Username not found"}), 404

    # So khớp mật khẩu
    if manager["password"] == password:  # Bạn có thể dùng hash để bảo mật hơn
        return jsonify({
            "success": True,
            "message": "Login successful",
            "data": {
                "fullname": manager["fullname"],
                "department_id": manager["department_id"],
                "username": manager["username"],
                "created_at": manager["created_at"]
            }
        }), 200
    else:
        return jsonify({"success": False, "message": "Incorrect password"}), 401
    
# ----------------------------------------------------------------
# Bộ nhớ tạm lưu trạng thái đã chào
greeted_employees = {}

def play_greeting(name, greeting_type="chào"):
    """Phát âm thanh chào mừng hoặc tạm biệt"""
    text = f"{greeting_type.capitalize()} {name}!"
    tts = gTTS(text, lang="vi")
    audio_file = "greeting.mp3"
    tts.save(audio_file)

    # Phát âm thanh bằng FFmpeg
    os.system(f"ffplay -nodisp -autoexit {audio_file}")
    
# ----------------------------------------------------------------
@app.route('/api/get_attendance', methods=['GET'])
def get_attendance():
    try:
        # Lấy ngày hiện tại
        today = datetime.now().strftime("%Y-%m-%d")

        # Lấy tất cả bản ghi trong ngày hôm nay
        records = list(data_collection.find({
            "timestamp": {"$regex": f"^{today}"}
        }))

        # Dữ liệu tạm để lưu kết quả
        attendance = {}

        # Lọc lần đầu tiên và cuối cùng cho mỗi ID
        for record in records:
            employee_id = record["id"]
            timestamp = record["timestamp"]

            if employee_id not in attendance:
                attendance[employee_id] = {
                    "first_seen": timestamp,
                    "last_seen": timestamp
                }
            else:
                # Cập nhật thời gian đầu tiên và cuối cùng
                if timestamp < attendance[employee_id]["first_seen"]:
                    attendance[employee_id]["first_seen"] = timestamp
                if timestamp > attendance[employee_id]["last_seen"]:
                    attendance[employee_id]["last_seen"] = timestamp

        # Lấy thông tin tên từ users collection
        attendance_list = []
        for emp_id, data in attendance.items():
            user = users_collection.find_one({"_id": emp_id})
            name = user["full_name"] if user else f"Unknown ({emp_id})"

            # Định dạng thời gian chuẩn
            first_seen_time = datetime.strptime(data["first_seen"], "%Y-%m-%d %H:%M:%S")
            last_seen_time = datetime.strptime(data["last_seen"], "%Y-%m-%d %H:%M:%S")
            
            # Kiểm tra xem đã chào trong ngày chưa
            if emp_id not in greeted_employees or greeted_employees[emp_id] != today:
                play_greeting(name, "Xin chào")
                greeted_employees[emp_id] = today  # Lưu trạng thái chào trong bộ nhớ

            attendance_list.append({
                "id": emp_id,
                "name": name,
                "first_seen": first_seen_time.strftime("%H:%M"),
                "last_seen": last_seen_time.strftime("%H:%M"),
            })

        return jsonify(attendance_list), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
# ----------------------------------------------------------------
def calculate_work_hours(entries):
    """Tính tổng giờ công trong ngày và ghi chú nếu đi muộn."""
    if len(entries) == 0:  # Không có dữ liệu
        return None, None, None, "Không có dữ liệu"
    elif len(entries) == 1:  # Chỉ có một mốc thời gian
        check_in = datetime.strptime(entries[0], "%Y-%m-%d %H:%M:%S")
        return check_in.strftime("%H:%M:%S"), None, None, "Chỉ có thời gian vào"

    check_in = min(entries)  # Lấy thời gian vào đầu tiên
    check_out = max(entries)  # Lấy thời gian ra cuối cùng
    check_in_dt = datetime.strptime(check_in, "%Y-%m-%d %H:%M:%S")
    check_out_dt = datetime.strptime(check_out, "%Y-%m-%d %H:%M:%S")
    total_hours = (check_out_dt - check_in_dt).total_seconds() / 3600

    # Ghi chú đi muộn nếu vào sau 08:00
    late_note = "Đi muộn" if check_in_dt.time() > datetime.strptime("08:00:00", "%H:%M:%S").time() else ""

    return check_in_dt.strftime("%H:%M:%S"), check_out_dt.strftime("%H:%M:%S"), total_hours, late_note

@app.route('/api/export_attendance', methods=['POST'])
def export_attendance():
    try:
        # Lấy thông tin từ yêu cầu API
        data = request.json
        month = data.get("month")  # Định dạng YYYY-MM
        camera_name = data.get("camera_name")

        if not month or not camera_name:
            return jsonify({"error": "Thiếu thông tin month hoặc camera_name"}), 400

        # Lấy danh sách nhân viên từ collection users
        users = list(users_collection.find())
        results = []

        # Map ngày trong tuần sang tiếng Việt
        weekday_map = {
            0: "Thứ 2",
            1: "Thứ 3",
            2: "Thứ 4",
            3: "Thứ 5",
            4: "Thứ 6",
            5: "Thứ 7",
            6: "Chủ nhật"
        }

        # Duyệt qua từng nhân viên
        for user in users:
            user_id = user["_id"]
            full_name = user["full_name"]

            # Lặp qua từng ngày trong tháng
            start_date = datetime.strptime(f"{month}-01", "%Y-%m-%d")
            end_date = (start_date + timedelta(days=31)).replace(day=1) - timedelta(days=1)

            current_date = start_date
            while current_date <= end_date:
                # Chỉ xét từ thứ 2 đến thứ 6
                if current_date.weekday() < 5:
                    date_str = current_date.strftime("%Y-%m-%d")
                    weekday_str = weekday_map[current_date.weekday()]

                    # Lấy dữ liệu từ camera_data
                    camera_data = data_collection.find({
                        "timestamp": {"$regex": f"^{date_str}"},
                        "id": user_id,
                        "camera_name": camera_name
                    })
                    timestamps = [entry["timestamp"] for entry in camera_data]
                    
                    # Tính giờ công và ghi chú
                    check_in, check_out, total_hours, note = calculate_work_hours(timestamps)

                    # Thêm vào kết quả
                    results.append({
                        "ID": user_id,
                        "Tên nhân viên": full_name,
                        "Ngày": date_str,
                        "Thứ": weekday_str,
                        "Thời gian vào": check_in,
                        "Thời gian ra": check_out,
                        "Tổng giờ công": total_hours,
                        "Ghi chú": note
                    })

                # Sang ngày tiếp theo
                current_date += timedelta(days=1)

        # Tạo đường dẫn tệp Excel động
        month_number = month.split("-")[1]  # Lấy số tháng
        file_suffix = f"t{int(month_number)}"  # Tạo tiền tố dạng "t1", "t2"
        output_file = f"/tmp/attendance_{file_suffix}.xlsx"  # Tên tệp

        # Tạo DataFrame và xuất ra tệp Excel
        df = pd.DataFrame(results)
        df.to_excel(output_file, index=False)

        # Trả về JSON chứa đường dẫn tệp
        return jsonify({"message": "Export successful", "file": output_file}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
# ----------------------------------------------------------------
if config.init_database:
    print("-" * 80)
    print("Initialize database")
    process_user_photos()
    update_all_faiss_index()

# Chạy ứng dụng Flask
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6123)