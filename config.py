from pymongo import MongoClient
from pathlib import Path
import os
from update_basicsr import update_import

# Update basicsr model
update_import(file_path="/usr/local/lib/python3.10/dist-packages/basicsr/data/degradations.py")
update_import(file_path="/home/pc/.conda/envs/nnq_env/lib/python3.10/site-packages/basicsr/data/degradations.py")

class Config:
    """ Class chứa toàn bộ cấu hình của ứng dụng """
    
    # Cấu hình MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    client = MongoClient(MONGO_URI)
    db = client["my_database"]

    # Các collection
    users_collection = db["users"]
    managers_collection = db["managers"]
    camera_collection = db["camera_information"]
    data_collection = db["camera_data"]

    vram_limit_for_FER = 3
    
    # Đường dẫn lưu file
    save_path = str(Path.home()) + "/nnq_static"

    # Chuyển đổi sang pathlib.Path để xử lý thư mục
    SAVE_PATH = Path(save_path)
    UPLOADS_PATH = SAVE_PATH / "uploads"
    DATABASE_PATH = SAVE_PATH / "data_base"

    # Tạo thư mục nếu chưa tồn tại
    for folder in [SAVE_PATH, UPLOADS_PATH, DATABASE_PATH]:
        Path(folder).mkdir(parents=True, exist_ok=True)

    init_database = True


# Tạo instance `config` để sử dụng trong toàn bộ project
config = Config()