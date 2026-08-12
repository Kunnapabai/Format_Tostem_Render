from __future__ import annotations
from flask import Flask, request, jsonify, send_file, render_template_string
from flask_cors import CORS
import os
import subprocess
import time
import uuid
import shutil
import logging
import json
from datetime import datetime
from werkzeug.utils import secure_filename
import sys
from pathlib import Path
from functools import wraps
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import psycopg2
from psycopg2.extras import RealDictCursor

# ฟังก์ชันเชื่อมต่อ PostgreSQL เก็บข้อมูล user account
def get_db_connection():
    """เชื่อมต่อกับ PostgreSQL Database"""
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    # ถ้าไม่มี DATABASE_URL ให้ใช้ JSON file (สำหรับ local development)
    if not DATABASE_URL:
        logger.warning("No DATABASE_URL found, using JSON file")
        return None
    
    # Render ใช้ postgres:// แต่ psycopg2 ต้องการ postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

# สร้างตารางเมื่อเริ่มต้นแอพ
def init_database():
    """สร้างตาราง users ถ้ายังไม่มี"""
    conn = get_db_connection()
    
    # ถ้าไม่มี database connection ให้ใช้ JSON file
    if not conn:
        init_users_file()
        return
    
    try:
        cur = conn.cursor()
        
        # สร้างตาราง users
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR(255) PRIMARY KEY,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # เพิ่ม admin account เริ่มต้นถ้ายังไม่มี
        cur.execute("""
            INSERT INTO users (username, password, role)
            VALUES ('admin', '1234', 'admin')
            ON CONFLICT (username) DO NOTHING
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")
        # Fallback to JSON file
        init_users_file()

# -------------------- Config & Globals --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


CORS(app)

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB
ALLOWED_EXTENSIONS = {'xlsx', 'pdf'}

RESULTS_FOLDER = 'results'
QUOTATION_FOLDER = 'quotations'
SITE_SURVEY_FOLDER = 'site_surveys'
TEMPLATE_FOLDER = 'templates'
SITE_SURVEY_IMAGES_FOLDER = 'site_survey_images'

BASE_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(SITE_SURVEY_IMAGES_FOLDER, exist_ok=True)

# ✅ Add permission check
try:
    test_file = os.path.join(SITE_SURVEY_IMAGES_FOLDER, '.test')
    with open(test_file, 'w') as f:
        f.write('test')
    os.remove(test_file)
    logger.info(f"✅ Write permission OK for {SITE_SURVEY_IMAGES_FOLDER}")
except Exception as e:
    logger.error(f"❌ No write permission for {SITE_SURVEY_IMAGES_FOLDER}: {e}")

# User Database File
USERS_FILE = BASE_DIR / 'users.json'

# Initialize users file if not exists
def init_users_file():
    """สร้างไฟล์ users.json ถ้ายังไม่มี และตรวจสอบ admin role"""
    if not os.path.exists(USERS_FILE):
        default_users = {
            'admin': {
                'password': '1234',
                'created_at': datetime.now().isoformat(),
                'role': 'admin'
            },
            'chonthichaphumphung@gmail.com': {
                'password': '1234',
                'created_at': datetime.now().isoformat(),
                'role': 'user'
            }
        }
        save_users(default_users)
        logger.info("✅ Created default users.json file")
    else:
        # ✅ ตรวจสอบและแก้ไข admin role ถ้าไม่ถูกต้อง
        users = load_users()
        updated = False
        
        if 'admin' in users and users['admin'].get('role') != 'admin':
            users['admin']['role'] = 'admin'
            updated = True
            logger.info("✅ Fixed admin user role")
        
        # ตรวจสอบ users อื่นๆ ที่ชื่อว่า 'admin' แต่ role ไม่ใช่ admin
        for username, user_info in users.items():
            if username.lower() == 'admin' and user_info.get('role') != 'admin':
                users[username]['role'] = 'admin'
                updated = True
                logger.info(f"✅ Fixed role for user: {username}")
        
        if updated:
            save_users(users)

def load_users():
    """โหลดข้อมูล users จาก PostgreSQL หรือ JSON file"""
    conn = get_db_connection()
    
    # ถ้าไม่มี database connection ให้ใช้ JSON file
    if not conn:
        try:
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading users from JSON: {e}")
            return {}
    
    # ใช้ PostgreSQL
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT username, password, role, created_at, updated_at
            FROM users
            ORDER BY created_at DESC
        """)
        
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        # แปลงเป็น format เดิมที่โค้ดใช้
        users = {}
        for row in rows:
            users[row['username']] = {
                'password': row['password'],
                'role': row['role'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
            }
        
        return users
    except Exception as e:
        logger.error(f"Error loading users from database: {e}")
        return {}

def save_users(users_data):
    """บันทึกข้อมูล users ลง PostgreSQL หรือ JSON file"""
    conn = get_db_connection()
    
    # ถ้าไม่มี database connection ให้ใช้ JSON file
    if not conn:
        try:
            with open(USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(users_data, f, ensure_ascii=False, indent=2)
            logger.info("✅ Users saved to JSON successfully")
            return True
        except Exception as e:
            logger.error(f"Error saving users to JSON: {e}")
            return False
    
    # ใช้ PostgreSQL
    try:
        cur = conn.cursor()
        
        for username, user_info in users_data.items():
            # ใช้ UPSERT (INSERT ... ON CONFLICT)
            cur.execute("""
                INSERT INTO users (username, password, role, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (username) 
                DO UPDATE SET 
                    password = EXCLUDED.password,
                    role = EXCLUDED.role,
                    updated_at = EXCLUDED.updated_at
            """, (
                username,
                user_info.get('password'),
                user_info.get('role', 'user'),
                user_info.get('created_at', datetime.now().isoformat()),
                user_info.get('updated_at', datetime.now().isoformat())
            ))
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Users saved to database successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving users to database: {e}")
        return False

# Initialize database (will fallback to JSON if no DATABASE_URL)
init_database()

# Update USERS dict to load from database or JSON
USERS = load_users()

from functools import wraps

def require_admin(f):
    """Decorator ตรวจสอบว่าเป็น admin หรือไม่"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        username = request.headers.get('X-Username')
        
        if not username:
            return jsonify({
                'success': False,
                'error': 'กรุณาเข้าสู่ระบบก่อน',
                'redirect': '/login'
            }), 401
        
        users = load_users()
        
        if username not in users:
            return jsonify({
                'success': False,
                'error': 'ไม่พบผู้ใช้ในระบบ',
                'redirect': '/login'
            }), 401
        
        user_role = users[username].get('role', 'user')
        
        if user_role != 'admin':
            return jsonify({
                'success': False,
                'error': 'คุณไม่มีสิทธิ์เข้าถึงหน้านี้ (ต้องเป็น Admin เท่านั้น)',
                'redirect': '/matrix'
            }), 403
        
        return f(*args, **kwargs)
    return decorated_function

# Check if main5.py exists and can be used
MAIN5_AVAILABLE = False
MAIN5_PATH = BASE_DIR / 'main5.py'

if os.path.exists(MAIN5_PATH):
    try:
        # Test if we can import main5.py and its dependencies
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
            
        import importlib.util
        spec = importlib.util.spec_from_file_location("main5", MAIN5_PATH)
        main5_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main5_module)
        
        # Check if required classes exist
        required_classes = ['PDFDataExtractor', 'ELEDataExtractor', 'DataComparator', 'CombinedDataProcessor']
        for cls_name in required_classes:
            if not hasattr(main5_module, cls_name):
                raise ImportError(f"Missing class: {cls_name}")
        
        # Test import of required sub-modules
        required_modules = ['insect_screen_full', 'color_comparison_full' ,'door_direction_full' ,'transom_comparison']
        for mod_name in required_modules:
            mod_path = BASE_DIR / f"{mod_name}.py"
            if not os.path.exists(mod_path):
                raise ImportError(f"Missing required file: {mod_name}.py")
        
        MAIN5_AVAILABLE = True
        logger.info("main5.py is available with all dependencies")
        
    except Exception as e:
        logger.warning(f"main5.py or dependencies unavailable: {e}")
        MAIN5_AVAILABLE = False
else:
    logger.warning("main5.py not found")

# Check if main6.py exists and can be used for quotation comparison
MAIN6_AVAILABLE = False
MAIN6_PATH = BASE_DIR / 'main6.py'

if os.path.exists(MAIN6_PATH):
    try:
        # Test if we can import main6.py and its dependencies
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
            
        import importlib.util
        spec = importlib.util.spec_from_file_location("main6", MAIN6_PATH)
        main6_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main6_module)
        
        # Check if required class exists
        if hasattr(main6_module, 'QuoteComparator'):
            MAIN6_AVAILABLE = True
            logger.info("main6.py is available with QuoteComparator class")
        else:
            raise ImportError("Missing QuoteComparator class in main6.py")
            
    except Exception as e:
        logger.warning(f"main6.py or dependencies unavailable: {e}")
        MAIN6_AVAILABLE = False
else:
    logger.warning("main6.py not found")

MAIN7_AVAILABLE = False
MAIN7_PATH = BASE_DIR / 'main7.py'

if os.path.exists(MAIN7_PATH):
    try:
        # Test if we can import main7.py and its dependencies
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
            
        import importlib.util
        spec = importlib.util.spec_from_file_location("main7", MAIN7_PATH)
        main7_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main7_module)
        
        # Check if required class exists
        if hasattr(main7_module, 'PDFExtractorWeb'):
            MAIN7_AVAILABLE = True
            logger.info("main7.py is available with PDFExtractorWeb class")
        else:
            raise ImportError("Missing PDFExtractorWeb class in main7.py")
            
    except Exception as e:
        logger.warning(f"main7.py or dependencies unavailable: {e}")
        MAIN7_AVAILABLE = False
else:
    logger.warning("main7.py not found")

MAIN8_AVAILABLE = False
MAIN8_PATH = BASE_DIR / 'main8.py'

if os.path.exists(MAIN8_PATH):
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        
        # Check required modules for main8
        required_main8_modules = {
            'site_survey_generator.py': False,
            'site_survey_image.py': False,
            'window_door_image_generator.py': False
        }
        
        for mod_file in required_main8_modules.keys():
            mod_path = BASE_DIR / mod_file
            if os.path.exists(mod_path):
                required_main8_modules[mod_file] = True
        
        # Import site_survey_generator module
        try:
            from site_survey_generator import (
                enhanced_process_quotation_file_with_smart_mosquito as process_quotation_file2,
                enhanced_generate_site_survey_report as generate_site_survey_report,
                DOCX_AVAILABLE,
                REPORTLAB_AVAILABLE
            )
            
            from site_survey_image import ImageSupportedSiteSurveyGenerator
            from window_door_image_generator import WindowDoorImageGenerator, generate_images_for_site_survey
            
            MAIN8_AVAILABLE = True
            logger.info("✅ main8.py (Site Survey Generator) is available with all dependencies")
            logger.info(f"   - DOCX support: {DOCX_AVAILABLE}")
            logger.info(f"   - PDF support: {REPORTLAB_AVAILABLE}")
            
        except ImportError as e:
            logger.warning(f"Failed to import main8 dependencies: {e}")
            MAIN8_AVAILABLE = False
            
    except Exception as e:
        logger.warning(f"main8.py or dependencies unavailable: {e}")
        MAIN8_AVAILABLE = False
else:
    logger.warning("main8.py not found")

if MAIN8_AVAILABLE:
    quotation_jobs = {}
    site_survey_jobs = {}
    uploaded_images_db = {}

# -------------------- Helpers --------------------
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def cleanup_old_files(hours: int = 1) -> None:
    """Clean up files older than `hours` hours"""
    try:
        current_time = time.time()
        expire = hours * 3600
        for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    file_path = os.path.join(folder, filename)
                    if os.path.isfile(file_path):
                        if current_time - os.path.getctime(file_path) > expire:
                            os.remove(file_path)
                            logger.info(f"Cleaned up old file: {file_path}")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

def load_html_template(template_name='matrix') -> str:
    template_files = {
        'login': 'login.html',
        'matrix': 'Excel-Matrix.html',
        'joint': 'Excel_Joint.html',
        'text-glass': 'text-glass.html',
        'text-glass2': 'text-glass2.html',
        'glass-check': 'glass-check.html',
        'sitewithele': 'site-with-ele.html',
        'Quotation': 'Quotation.html',
        'Generate': 'Generate.html',
        'user-management': 'user-management.html'
    }
    try:
        filename = template_files.get(template_name)
        if filename and os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return f.read()
        return f"""
        <html><body>
        <h1>Error: {filename} not found</h1>
        <p>Please make sure {filename} is in the same directory as server.py</p>
        <p>Current directory: {os.getcwd()}</p>
        <p>Files in directory: {os.listdir('.')}</p>
        <p><a href="/">← กลับหน้าหลัก</a></p>
        </body></html>
        """
    except Exception as e:
        return f"<html><body><h1>Error loading template: {e}</h1></body></html>"

# -------------------- Subprocess wrappers --------------------
def run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    
    logger.info(f"Running command: {' '.join(cmd)}")
    logger.info(f"Working directory: {BASE_DIR}")
    logger.info(f"Python executable: {PYTHON}")
    
    result = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True
    )
    
    logger.info(f"Command finished with return code: {result.returncode}")
    if result.stdout:
        logger.info(f"STDOUT: {result.stdout[:500]}...")
    if result.stderr:
        logger.error(f"STDERR: {result.stderr}")
    
    return result

# -------------------- Quotation Processing via direct import --------------------
def process_quotation_comparison_direct_import(file1_path: str, file2_path: str):
    """Process quotation comparison by directly importing main6.py"""
    if not MAIN6_AVAILABLE:
        return None, "main6.py is not available"
    
    try:
        logger.info("Starting quotation comparison via direct import")
        
        # Import main6.py module
        import importlib.util
        spec = importlib.util.spec_from_file_location("main6", MAIN6_PATH)
        main6 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main6)
        
        # Create QuoteComparator instance
        comparator = main6.QuoteComparator()
        
        # Load both quotes
        quote1 = comparator.load_quote(file1_path, 'file1')
        quote2 = comparator.load_quote(file2_path, 'file2')
        
        if not quote1['items'] and not quote2['items']:
            return None, "ไม่สามารถดึงข้อมูลจากไฟล์ PDF ได้"
        
        # Perform comparison
        comparison = comparator.compare_quotes('file1', 'file2')
        
        # Generate statistics
        stats = {
            'total_items_quote1': len(quote1['items']),
            'total_items_quote2': len(quote2['items']),
            'items_not_matching': len(comparison['item_differences']['modified_items']) +
                                  len(comparison['item_differences']['added_items']) +
                                  len(comparison['item_differences']['removed_items']),
        }

        final_total1 = quote1['summary'].get('final_total', 0)
        final_total2 = quote2['summary'].get('final_total', 0)

        stats['total_price_difference'] = final_total2 - final_total1
        stats['total_price_percent_change'] = (
            (final_total2 - final_total1) / final_total1 * 100 if final_total1 else 0
        )

        # Prepare result
        result = {
            'success': True,
            'summary_stats': stats,
            'item_differences': comparison['item_differences'],
            'summary_differences': comparison['summary_differences'],
            'header_differences': comparison['header_differences'],
            'quote1_info': comparison['quote1_info'],
            'quote2_info': comparison['quote2_info'],
            'quote1_data': quote1,
            'quote2_data': quote2
        }
        
        return result, None
        
    except Exception as e:
        logger.exception("Error in quotation comparison processing")
        return None, f"เกิดข้อผิดพลาดในการประมวลผล: {str(e)}"

# -------------------- Site+ELE Processing via direct import --------------------

def process_site_ele_direct_import(site_file_path: str, ele_file_path: str):
    """Process Site Survey vs ELE comparison by directly importing main5.py"""
    if not MAIN5_AVAILABLE:
        return None, "main5.py is not available"
    
    try:
        logger.info("Starting Site+ELE comparison via direct import")
        
        # Import required modules from main5.py
        import importlib.util
        spec = importlib.util.spec_from_file_location("main5", MAIN5_PATH)
        main5 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main5)
        
        # Import the required processing modules
        try:
            # Import sub-modules by ensuring they're in sys.path
            if str(BASE_DIR) not in sys.path:
                sys.path.insert(0, str(BASE_DIR))
            
            import insect_screen_full
            import color_comparison_full
            import door_direction_full
            import transom_comparison
            
        except ImportError as e:
            logger.error(f"Required modules missing: {e}")
            return None, f"Missing required modules. Please ensure these files exist: insect_screen_full.py, color_comparison_full.py, door_direction_full.py, transom_comparison.py"
        
        # Extract data using main5.py classes
        site_data = main5.PDFDataExtractor.extract_site_survey_data(site_file_path)
        ele_data = main5.ELEDataExtractor.extract_ele_data(ele_file_path)
        
        if not site_data and not ele_data:
            return None, "ไม่สามารถดึงข้อมูลจากไฟล์ PDF ได้"
        
        # **FIX: รวบรวม token usage จาก main5.py**
        site_tokens = getattr(main5.PDFDataExtractor, 'site_tokens_used', 0)
        total_tokens = getattr(main5.ELEDataExtractor, 'total_tokens_used', site_tokens)
        ele_tokens = total_tokens - site_tokens
        
        # คำนวณค่าใช้จ่าย
        def calculate_estimated_cost(total_tokens):
            """Calculate estimated cost based on Gemini 2.5 Pro pricing"""
            if total_tokens == 0:
                return 0.0
            
            # Gemini 2.5 Pro pricing (approximate for vision + text)
            # Vision tasks: ~$3.00 per 1M tokens
            cost_per_million_tokens = 3.00
            return (total_tokens / 1_000_000) * cost_per_million_tokens
        
        estimated_cost = calculate_estimated_cost(total_tokens)
        
        # สร้าง token_usage object
        token_usage = {
            'total_tokens': int(total_tokens),
            'site_tokens': int(site_tokens),
            'ele_tokens': int(ele_tokens), 
            'estimated_cost': float(estimated_cost),
            'api_enabled': True  # ถ้าใช้ main5.py แปลว่า API enabled
        }
        
        logger.info(f"=== TOKEN USAGE FROM SERVER ===")
        logger.info(f"Site tokens: {site_tokens}")
        logger.info(f"ELE tokens: {ele_tokens}")
        logger.info(f"Total tokens: {total_tokens}")
        logger.info(f"Estimated cost: ${estimated_cost:.4f}")
        logger.info(f"Token usage object: {token_usage}")
        
        # Process additional data first
        try:
            # เพิ่มการประมวลผล sub_panels
            sub_panel_results = main5.process_sub_panels(site_file_path, ele_file_path)
        except Exception as e:
            logger.warning(f"Sub-panel processing failed: {e}")
            sub_panel_results = {'success': False, 'results': []}
            
        try:
            insect_screen_results = insect_screen_full.process_insect_screens(site_file_path, ele_file_path)
        except Exception as e:
            logger.warning(f"Insect screen processing failed: {e}")
            insect_screen_results = {'success': False, 'results': []}
            
        try:
            color_results = color_comparison_full.process_colors(site_file_path, ele_file_path)
        except Exception as e:
            logger.warning(f"Color processing failed: {e}")
            color_results = {'success': False, 'results': []}

        try:
            door_direction_results = door_direction_full.process_door_directions(site_file_path, ele_file_path)
        except Exception as e:
            logger.warning(f"Direction processing failed: {e}")
            door_direction_results = {'success': False, 'results': []}

        try:
            transom_results = transom_comparison.process_transoms(ele_file_path, use_context_detection=False)
        except Exception as e:
            logger.warning(f"Transom processing failed: {e}")
            transom_results = {'success': False, 'results': []}
        
        # Use the correct method name from main5.py with proper parameters
        try:
            # Try the integrated method first with correct parameter order
            comparison_results = main5.DataComparator.compare_data_with_sub_panels_integrated(
                site_data, ele_data, sub_panel_results, color_results, 
                insect_screen_results, door_direction_results, transom_results
            )
        except AttributeError:
            # Fallback to basic comparison if integrated method doesn't exist
            try:
                comparison_results = main5.DataComparator.compare_data_with_colors_and_screens(
                    site_data, ele_data, color_results, insect_screen_results, 
                    door_direction_results, transom_results
                )
            except AttributeError:
                # If neither method exists, create a basic comparison
                logger.warning("No comparison method found in DataComparator, creating basic comparison")
                comparison_results = create_basic_comparison(site_data, ele_data)
        
        # Create combined comparison
        combined_processor = main5.CombinedDataProcessor()
        
        sub_panel_data = sub_panel_results.get('results', []) if sub_panel_results.get('success') else []
        insect_screen_data = insect_screen_results.get('results', []) if insect_screen_results.get('success') else []
        color_data = color_results.get('results', []) if color_results.get('success') else []
        door_direction_data = door_direction_results.get('results', []) if door_direction_results.get('success') else []
        transom_data = transom_results.get('results', []) if transom_results.get('success') else []    
        
        combined_comparison = combined_processor.create_combined_comparison(
            site_data, ele_data, sub_panel_data, insect_screen_data, 
            color_data, door_direction_data, transom_data
        )
        
        # Generate summary
        total_items = len(site_data)
        
        # Count opening rows and sub-panel rows in main table
        opening_rows = len([r for r in comparison_results if r.get('Row_Type') == 'Opening'])
        sub_panel_rows = len([r for r in comparison_results if r.get('Row_Type') == 'Sub_Panel'])
        
        # If we don't have Row_Type (basic comparison), count all as opening rows
        if opening_rows == 0 and sub_panel_rows == 0:
            opening_rows = len(comparison_results)
        
        main_perfect_matches = len([r for r in comparison_results 
                                   if r.get('Overall_Status', '').startswith('✅')])
        
        total_combined_rows = len(combined_comparison)
        
        status_counts = {}
        for row in combined_comparison:
            status = row.get('Status', 'Unknown')
            if '✅' in status:
                status_type = 'Match'
            elif '❌' in status:
                status_type = 'Mismatch'
            else:
                status_type = 'Other'
            status_counts[status_type] = status_counts.get(status_type, 0) + 1
        
        summary = {
            'total_items': total_items,
            'main_perfect_matches': main_perfect_matches,
            'main_mismatch_items': len(comparison_results) - main_perfect_matches,
            'main_success_rate': round((main_perfect_matches / len(comparison_results) * 100), 1) if comparison_results else 0,
            'total_combined_rows': total_combined_rows,
            'opening_rows': opening_rows,
            'sub_panel_rows': sub_panel_rows,
            'status_distribution': status_counts,
            'sub_panel_count': len(sub_panel_data),  # เพิ่ม
            'insect_screen_count': len(insect_screen_data),
            'color_count': len(color_data),
            'door_direction_count': len(door_direction_data),
            'transom_count': len(transom_data)
        }
        
        # **FIX: เพิ่ม token_usage ใน result**
        result = {
            'success': True,
            'summary': summary,
            'results': comparison_results,
            'site_data': site_data,
            'ele_data': ele_data,
            'combined_comparison': combined_comparison,
            'sub_panel_results': sub_panel_data,  # เพิ่ม
            'insect_screen_results': insect_screen_data,
            'color_results': color_data,
            'door_direction_results': door_direction_data,
            'transom_results': transom_data,
            'token_usage': token_usage  # **CRITICAL: เพิ่มบรรทัดนี้**
        }
        
        logger.info(f"=== FINAL RESULT STRUCTURE ===")
        logger.info(f"Result keys: {list(result.keys())}")
        logger.info(f"token_usage in result: {'token_usage' in result}")
        logger.info(f"Final token_usage: {result.get('token_usage')}")
        
        return result, None
        
    except Exception as e:
        logger.exception("Error in Site+ELE processing")
        return None, f"เกิดข้อผิดพลาดในการประมวลผล: {str(e)}"

def create_basic_comparison(site_data, ele_data):
    """Create a basic comparison when advanced methods are not available"""
    results = []
    
    # Create ELE lookup
    ele_lookup = {}
    for item in ele_data:
        ref = item["Ref"]
        if ref not in ele_lookup:
            ele_lookup[ref] = []
        ele_lookup[ref].append(item)
    
    # Process site data
    for s in site_data:
        ref = s["Ref"]
        ele_items = ele_lookup.get(ref, [])
        
        if not ele_items:
            # Missing in ELE
            result = {
                **s,
                "Ele_Wo": "-", 
                "Ele_Ho": "-",
                "Element_Type": "-",
                "Series_ELE": "-",
                "Site_Color": "-",
                "ELE_Color": "-",
                "Site_Screen": "-",
                "ELE_Screen": "-",
                "Wo_Status": "❌ Missing in ELE",
                "Ho_Status": "❌ Missing in ELE",
                "Series_Match": "❌ Missing data",
                "Type_Match": "❌ Missing data",
                "Color_Match": "❌ Missing data",
                "Screen_Match": "❌ Missing data",
                "Overall_Status": "❌ Missing in ELE",
                "Notes": "ไม่พบข้อมูลใน ELE",
                "Row_Type": "Opening"
            }
            results.append(result)
            continue
        
        # Compare with first ELE item
        ele = ele_items[0]
        
        # Basic dimension comparison (±15mm tolerance)
        wo_match = abs(s["Survey_Wo"] - ele["Ele_Wo"]) <= 15 if s["Survey_Wo"] and ele["Ele_Wo"] else False
        ho_match = abs(s["Survey_Ho"] - ele["Ele_Ho"]) <= 15 if s["Survey_Ho"] and ele["Ele_Ho"] else False
        
        # Basic type comparison
        type_match = str(s.get("Product Type", "")).lower().strip() == str(ele.get("Element_Type", "")).lower().strip()
        
        # Generate notes
        notes = []
        if not wo_match:
            diff = abs(s["Survey_Wo"] - ele["Ele_Wo"]) if s["Survey_Wo"] and ele["Ele_Wo"] else 0
            notes.append(f"Width diff: {diff}mm")
        if not ho_match:
            diff = abs(s["Survey_Ho"] - ele["Ele_Ho"]) if s["Survey_Ho"] and ele["Ele_Ho"] else 0
            notes.append(f"Height diff: {diff}mm")
        if not type_match:
            notes.append("Type mismatch")
        
        # Determine overall status
        if wo_match and ho_match and type_match:
            overall_status = "✅ Perfect Match"
        else:
            errors = []
            if not wo_match or not ho_match:
                errors.append("Size")
            if not type_match:
                errors.append("Type")
            overall_status = f"❌ {' + '.join(errors)} Mismatch"
        
        result = {
            **s,
            "Ele_Wo": ele["Ele_Wo"],
            "Ele_Ho": ele["Ele_Ho"],
            "Element_Type": ele["Element_Type"],
            "Series_ELE": ele.get("Series", "-"),
            "Site_Color": "-",
            "ELE_Color": "-",
            "Site_Screen": "-",
            "ELE_Screen": "-",
            "Wo_Status": "✅ Match" if wo_match else "❌ Mismatch",
            "Ho_Status": "✅ Match" if ho_match else "❌ Mismatch",
            "Type_Match": "✅ Match" if type_match else "❌ Mismatch",
            "Series_Match": "-",
            "Color_Match": "-",
            "Screen_Match": "-",
            "Overall_Status": overall_status,
            "Notes": "; ".join(notes) if notes else "OK",
            "Row_Type": "Opening"
        }
        results.append(result)
    
    # Add ELE items missing in site
    site_refs = {item["Ref"] for item in site_data}
    for item in ele_data:
        if item["Ref"] not in site_refs:
            result = {
                "Ref": item["Ref"],
                "Series": "-",
                "Product Type": "-",
                "Survey_Wo": "-",
                "Survey_Ho": "-",
                "Insect_Screen": "-",
                "Page": "-",
                "Ele_Wo": item["Ele_Wo"],
                "Ele_Ho": item["Ele_Ho"],
                "Element_Type": item["Element_Type"],
                "Series_ELE": item.get("Series", "-"),
                "Site_Color": "-",
                "ELE_Color": "-",
                "Site_Screen": "-",
                "ELE_Screen": "-",
                "Wo_Status": "❌ Missing in Site",
                "Ho_Status": "❌ Missing in Site",
                "Series_Match": "❌ Missing data",
                "Type_Match": "❌ Missing data",
                "Color_Match": "❌ Missing data",
                "Screen_Match": "❌ Missing data",
                "Overall_Status": "❌ Missing in Site",
                "Notes": "ไม่พบข้อมูลใน Site Survey",
                "Row_Type": "Opening"
            }
            results.append(result)
    
    return results

# -------------------- Original processing functions (unchanged) --------------------
def process_comparison_with_main_py(source_type: str, source_data: str, source_pdf_path: str, target_pdf_path: str, start_page: int = 1):
    """Process comparison using main.py with different modes"""
    try:
        start_time = time.time()

        main_py_path = BASE_DIR / 'main.py'
        logger.info(f"main.py path: {main_py_path}")
        logger.info(f"main.py exists: {os.path.exists(main_py_path)}")

        if not os.path.exists(main_py_path):
            return None, f'ไม่พบไฟล์ main.py ที่ {main_py_path}'

        if source_type == 'text':
            main4_py_path = BASE_DIR / 'main4.py'
            if os.path.exists(main4_py_path):
                logger.info(f"Processing text vs PDF comparison with main4.py")
                cmd = [
                    PYTHON, str(main4_py_path),
                    '--mode', 'text_vs_pdf',
                    '--text', source_data,
                    '--target-pdf', target_pdf_path,
                    '--target-start-page', str(start_page)
                ]
            else:
                logger.info(f"Processing text vs PDF comparison with main.py (new format)")
                cmd = [
                    PYTHON, str(main_py_path),
                    '--mode', 'text_vs_pdf',
                    '--text', source_data,
                    '--target-pdf', target_pdf_path,
                    '--target-start-page', str(start_page)
                ]
        elif source_type == 'pdf':
            main4_py_path = BASE_DIR / 'main4.py'
            if os.path.exists(main4_py_path):
                logger.info(f"Processing PDF vs PDF comparison with main4.py")
                cmd = [
                    PYTHON, str(main4_py_path),
                    '--mode', 'pdf_vs_pdf',
                    '--source-pdf', source_pdf_path,
                    '--target-pdf', target_pdf_path,
                    '--source-start-page', '3',
                    '--target-start-page', str(start_page)
                ]
            else:
                logger.info(f"Processing PDF vs PDF comparison with main.py (new format)")
                cmd = [
                    PYTHON, str(main_py_path),
                    '--mode', 'pdf_vs_pdf',
                    '--source-pdf', source_pdf_path,
                    '--target-pdf', target_pdf_path,
                    '--source-start-page', '3',
                    '--target-start-page', str(start_page)
                ]
        else:
            return None, f'ไม่รองรับ source type: {source_type}'

        result = run_subprocess(cmd)
        processing_time = time.time() - start_time

        # Clean up PDF files
        try:
            if os.path.exists(target_pdf_path):
                os.remove(target_pdf_path)
                logger.info(f"Cleaned up target PDF file: {target_pdf_path}")
            if source_type == 'pdf' and os.path.exists(source_pdf_path):
                os.remove(source_pdf_path)
                logger.info(f"Cleaned up source PDF file: {source_pdf_path}")
        except Exception as cleanup_error:
            logger.warning(f"Could not remove PDF files: {cleanup_error}")

        if result.returncode != 0:
            error_msg = f'Script failed with return code {result.returncode}'
            if result.stderr:
                error_msg += f': {result.stderr}'
            logger.error(error_msg)
            return None, error_msg

        # Parse JSON output
        try:
            if not result.stdout.strip():
                return None, 'Script returned empty output'
                
            logger.info(f"Parsing JSON output: {result.stdout[:200]}...")
            output = json.loads(result.stdout.strip())
            
            if 'error' in output:
                return None, output['error']
            
            output['processing_time'] = processing_time
            return output, None
            
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON output: %s", e)
            logger.error("Raw output was: %s", result.stdout)
            return None, f'Invalid JSON response from script: {str(e)}'

    except Exception as e:
        logger.exception("Unexpected error in comparison processing")
        return None, f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'

# -------------------- Matrix Mode Processing --------------------
def process_matrix_file_with_main_py(input_path: str, job_id: str, original_filename: str | None):
    try:
        start_time = time.time()

        # Try new format first
        cmd_new = [
            PYTHON, str(BASE_DIR / 'main.py'),
            '--mode', 'matrix',
            '--input', input_path,
            '--job-id', job_id,
            '--output-dir', OUTPUT_FOLDER
        ]
        if original_filename:
            cmd_new += ['--original-filename', original_filename]

        # Legacy format fallback
        cmd_legacy = [
            PYTHON, str(BASE_DIR / 'main.py'),
            '--input', input_path,
            '--job-id', job_id,
            '--output-dir', OUTPUT_FOLDER
        ]
        if original_filename:
            cmd_legacy += ['--original-filename', original_filename]

        result = run_subprocess(cmd_new)
        
        if result.returncode == 2 and '--mode' in ' '.join(cmd_new):
            logger.info("New format failed, trying legacy format...")
            result = run_subprocess(cmd_legacy)

        processing_time = time.time() - start_time

        try:
            os.remove(input_path)
        except Exception:
            pass

        if result.returncode != 0:
            logger.error("Processing failed with main.py: %s", result.stderr)
            return None, f'เกิดข้อผิดพลาดในการประมวลผล: {result.stderr}'

        output_lines = result.stdout.strip().split('\n')
        json_output = None
        for line in reversed(output_lines):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    json_output = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass

        if not json_output:
            return None, 'ไม่พบผลลัพธ์จาก main.py'

        price_file = os.path.join(OUTPUT_FOLDER, f'Price_{job_id}.xlsx')
        type_file = os.path.join(OUTPUT_FOLDER, f'Type_{job_id}.xlsx')

        if not os.path.exists(price_file):
            return None, 'ไม่พบไฟล์ Price ที่สร้างขึ้น'
        if not os.path.exists(type_file):
            return None, 'ไม่พบไฟล์ Type ที่สร้างขึ้น'

        return {
            'job_id': job_id,
            'total_records': json_output.get('total_records', 0),
            'price_records': json_output.get('total_records', 0),
            'type_records': json_output.get('processed_sheets', 0),
            'processed_sheets': json_output.get('processed_sheets', 0),
            'processing_time': processing_time,
            'message': 'ประมวลผลสำเร็จ',
            'skipped_sheets': json_output.get('skipped_sheets', []),
            'warnings': json_output.get('warnings', [])
        }, None

    except Exception as e:
        logger.exception("Unexpected error with main.py")
        return None, f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'

# -------------------- Joint Mode Processing --------------------
def process_joint_file_with_main_py(input_path: str, job_id: str):
    """Handle joint mode file processing - FIXED VERSION"""
    try:
        start_time = time.time()

        # Check if main2.py exists for joint processing
        main2_py_path = BASE_DIR / 'main2.py'
        
        if os.path.exists(main2_py_path):
            # Use main2.py directly for joint processing
            logger.info(f"Processing Joint file with main2.py")
            cmd = [
                PYTHON, str(main2_py_path),
                input_path,
                job_id
            ]
        else:
            # Fallback: Try to use main.py without --mode if it supports joint processing
            logger.info(f"Processing Joint file with main.py (legacy format)")
            cmd = [
                PYTHON, str(BASE_DIR / 'main.py'),
                '--input', input_path,
                '--job-id', job_id,
                '--output-dir', OUTPUT_FOLDER
            ]
        
        result = run_subprocess(cmd)
        processing_time = time.time() - start_time

        # Clean up input file
        try:
            os.remove(input_path)
        except Exception:
            pass

        if result.returncode != 0:
            logger.error("Processing failed: %s", result.stderr)
            return None, f'เกิดข้อผิดพลาดในการประมวลผล: {result.stderr}'

        # Try to parse JSON output first (new format)
        output_lines = result.stdout.strip().split('\n')
        json_output = None
        
        for line in reversed(output_lines):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    json_output = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass

        if json_output:
            # New JSON format response
            return {
                'job_id': job_id,
                'total_records': json_output.get('price_records', 0) + json_output.get('type_records', 0),
                'price_records': json_output.get('price_records', 0),
                'type_records': json_output.get('type_records', 0),
                'processed_sheets': 1,
                'processing_time': processing_time,
                'message': 'ประมวลผลสำเร็จ'
            }, None
        else:
            # Legacy format for backward compatibility (main2.py output)
            price_file = None
            type_file = None
            price_count = 0
            type_count = 0

            for line in output_lines:
                if line.startswith('MOVED_PRICE:'):
                    price_file = line.split(':', 1)[1]
                elif line.startswith('MOVED_TYPE:'):
                    type_file = line.split(':', 1)[1]
                elif line.startswith('PRICE_COUNT:'):
                    try:
                        price_count = int(line.split(':', 1)[1])
                    except (ValueError, IndexError):
                        price_count = 0
                elif line.startswith('TYPE_COUNT:'):
                    try:
                        type_count = int(line.split(':', 1)[1])
                    except (ValueError, IndexError):
                        type_count = 0

            # Move files to output directory with job_id naming
            if price_file and os.path.exists(price_file):
                shutil.move(price_file, os.path.join(OUTPUT_FOLDER, f'Price_{job_id}.xlsx'))
            if type_file and os.path.exists(type_file):
                shutil.move(type_file, os.path.join(OUTPUT_FOLDER, f'Type_{job_id}.xlsx'))

            # If no explicit counts were provided, try to count from files
            if price_count == 0 and os.path.exists(os.path.join(OUTPUT_FOLDER, f'Price_{job_id}.xlsx')):
                try:
                    import pandas as pd
                    df = pd.read_excel(os.path.join(OUTPUT_FOLDER, f'Price_{job_id}.xlsx'))
                    price_count = len(df)
                except Exception:
                    price_count = 0

            if type_count == 0 and os.path.exists(os.path.join(OUTPUT_FOLDER, f'Type_{job_id}.xlsx')):
                try:
                    import pandas as pd
                    df = pd.read_excel(os.path.join(OUTPUT_FOLDER, f'Type_{job_id}.xlsx'))
                    type_count = len(df)
                except Exception:
                    type_count = 0

            return {
                'job_id': job_id,
                'total_records': price_count + type_count,
                'price_records': price_count,
                'type_records': type_count,
                'processed_sheets': 1,
                'processing_time': processing_time,
                'message': 'ประมวลผลสำเร็จ'
            }, None

    except Exception as e:
        logger.exception("Unexpected error with joint processing")
        return None, f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'

# -------------------- PDF Format Mode Processing --------------------
def process_pdf_file_with_main_py(input_path: str, start_page: int, job_id: str):
    try:
        start_time = time.time()

        # Try main3.py first if available
        main3_py_path = BASE_DIR / 'main3.py'
        if os.path.exists(main3_py_path):
            logger.info(f"Processing PDF file with main3.py")
            cmd = [PYTHON, str(main3_py_path), input_path, str(start_page), job_id]
        else:
            # Use main.py with new format
            logger.info(f"Processing PDF file with main.py (new format)")
            cmd = [
                PYTHON, str(BASE_DIR / 'main.py'),
                '--mode', 'text-glass',
                '--input', input_path,
                '--start-page', str(start_page),
                '--job-id', job_id,
                '--output-dir', OUTPUT_FOLDER
            ]
        
        result = run_subprocess(cmd)
        processing_time = time.time() - start_time

        try:
            os.remove(input_path)
        except Exception:
            pass

        if result.returncode != 0:
            logger.error("Processing failed: %s", result.stderr)
            return None, f'เกิดข้อผิดพลาดในการประมวลผล: {result.stderr}'

        output_lines = result.stdout.strip().split('\n')
        json_output = None
        for line in reversed(output_lines):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    json_output = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass

        if not json_output:
            return None, 'ไม่พบผลลัพธ์จากการประมวลผล'
        if 'error' in json_output:
            return None, json_output['error']

        return {
            'success': True,
            'data': json_output,
            'processing_time': processing_time,
            'message': f"ประมวลผลสำเร็จ: พบ {json_output.get('total_references', 0)} Reference Code และ {json_output.get('total_glass', 0)} GLASS"
        }, None

    except Exception as e:
        logger.exception("Unexpected error in PDF processing")
        return None, f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'
    
def process_pdf_with_site_survey_direct_import(pdf_file_path: str, start_page: int, site_survey_path: str = None):
    """Process PDF extraction with site survey using main7.py"""
    if not MAIN7_AVAILABLE:
        return None, "main7.py is not available"
    
    try:
        logger.info("Starting PDF extraction with site survey via direct import")
        
        # Import main7.py module
        import importlib.util
        spec = importlib.util.spec_from_file_location("main7", MAIN7_PATH)
        main7 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main7)
        
        # Create PDFExtractorWeb instance
        extractor = main7.PDFExtractorWeb()
        
        # Extract data from file
        result = extractor.extract_data_from_file(pdf_file_path, start_page, site_survey_path)
        
        if 'error' in result:
            return None, result['error']
        
        # Prepare result with success flag
        final_result = {
            'success': True,
            'data': result
        }
        
        return final_result, None
        
    except Exception as e:
        logger.exception("Error in PDF extraction with site survey processing")
        return None, f"เกิดข้อผิดพลาดในการประมวลผล: {str(e)}"

def handle_main7_upload():
    """Handle main7.py PDF upload with optional site survey"""
    if not MAIN7_AVAILABLE:
        return jsonify({'error': 'Text Glass (2) not available. Please check main7.py and dependencies.'}), 400
    
    pdf_file = request.files['file']
    site_survey_file = request.files.get('site_survey')  # Optional
    
    if pdf_file.filename == '':
        return jsonify({'error': 'กรุณาเลือกไฟล์ PDF หลัก'}), 400
    
    # Validate main PDF file
    if not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'กรุณาเลือกไฟล์ PDF เท่านั้น'}), 400
    
    # Validate site survey file if provided
    if site_survey_file and site_survey_file.filename and not site_survey_file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'ไฟล์ Site Survey ต้องเป็น PDF เท่านั้น'}), 400
    
    # Get start page
    start_page = int(request.form.get('start_page', 1))
    
    # Save files securely
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = str(uuid.uuid4())[:8]
    job_id = f"{timestamp}_{random_suffix}"
    
    pdf_filename = f"{job_id}_main_{secure_filename(pdf_file.filename)}"
    pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
    pdf_file.save(pdf_path)
    
    site_survey_path = None
    if site_survey_file and site_survey_file.filename:
        site_survey_filename = f"{job_id}_site_{secure_filename(site_survey_file.filename)}"
        site_survey_path = os.path.join(UPLOAD_FOLDER, site_survey_filename)
        site_survey_file.save(site_survey_path)
        logger.info(f"Site survey file saved: {site_survey_path}")
    
    # Process using main7.py
    result, error = process_pdf_with_site_survey_direct_import(pdf_path, start_page, site_survey_path)
    
    # Clean up uploaded files
    try:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        if site_survey_path and os.path.exists(site_survey_path):
            os.remove(site_survey_path)
    except Exception as e:
        logger.warning(f"Could not remove temporary files: {e}")
    
    if error:
        return jsonify({'error': error}), 500
    
    return jsonify(result)

# -------------------- Routes --------------------

@app.route('/')
def index_redirect():
    """หน้าแรก - แสดงหน้า Login"""
    cleanup_old_files()
    html_template = load_html_template('login')
    return render_template_string(html_template)

@app.route('/login')
def login_page():
    """หน้า Login"""
    cleanup_old_files()
    html_template = load_html_template('login')
    return render_template_string(html_template)

@app.route('/api/login', methods=['POST'])
def login():
    """API สำหรับเข้าสู่ระบบ"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        # โหลด users จากไฟล์
        users = load_users()
        
        # ตรวจสอบ username และ password
        if username in users and users[username]['password'] == password:
            logger.info(f"Login successful for user: {username}")
            return jsonify({
                'success': True,
                'message': 'เข้าสู่ระบบสำเร็จ',
                'username': username
            })
        else:
            logger.warning(f"Failed login attempt for user: {username}")
            return jsonify({
                'success': False,
                'message': 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง'
            }), 401
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({
            'success': False,
            'message': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    """API สำหรับออกจากระบบ"""
    return jsonify({
        'success': True,
        'message': 'ออกจากระบบสำเร็จ'
    })

def send_password_email(to_email, password):
    """
    ส่งอีเมลรหัสผ่านไปยังผู้ใช้
    
    ⚠️ สำคัญ: คุณต้องตั้งค่าเหล่านี้:
    1. ใช้ Gmail App Password (ไม่ใช่รหัสผ่านปกติ)
    2. ตั้งค่าที่ https://myaccount.google.com/apppasswords
    3. เปลี่ยน SMTP_EMAIL และ SMTP_PASSWORD ด้านล่าง
    """
    
    # ⚠️ กรุณาเปลี่ยนค่าเหล่านี้ตามของคุณ
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 587
    SMTP_EMAIL = 'chonthichaphumphung@gmail.com' 
    SMTP_PASSWORD = 'Ton20072003'  
    
    # สร้างข้อความอีเมล
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'รหัสผ่านของคุณ - TOSTEM Work Process'
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    
    # สร้าง HTML content
    html_content = f"""
    <html>
    <head>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #f5f5f5;
                padding: 20px;
            }}
            .container {{
                max-width: 600px;
                margin: 0 auto;
                background: white;
                border-radius: 10px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
            }}
            .content {{
                padding: 30px;
            }}
            .password-box {{
                background: #f8f9fa;
                border: 2px dashed #667eea;
                border-radius: 8px;
                padding: 20px;
                text-align: center;
                margin: 20px 0;
            }}
            .password {{
                font-size: 24px;
                font-weight: bold;
                color: #667eea;
                letter-spacing: 2px;
            }}
            .footer {{
                background: #f8f9fa;
                padding: 20px;
                text-align: center;
                font-size: 12px;
                color: #666;
            }}
            .btn {{
                display: inline-block;
                padding: 12px 30px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                text-decoration: none;
                border-radius: 5px;
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔐 รหัสผ่านของคุณ</h1>
                <p>TOSTEM Work Process System</p>
            </div>
            <div class="content">
                <p>สวัสดีครับ,</p>
                <p>คุณได้ขอรหัสผ่านสำหรับเข้าสู่ระบบ TOSTEM Work Process</p>
                
                <div class="password-box">
                    <p style="margin: 0 0 10px 0; color: #666;">รหัสผ่านของคุณคือ:</p>
                    <div class="password">{password}</div>
                </div>
                
                <p style="color: #d32f2f; font-weight: bold;">⚠️ โปรดเก็บรหัสผ่านนี้ไว้เป็นความลับ</p>
                <p style="font-size: 14px; color: #666;">หากคุณไม่ได้ขอรหัสผ่าน กรุณาติดต่อผู้ดูแลระบบทันที</p>
                
                <div style="text-align: center;">
                    <a href="http://localhost:5000/login" class="btn">เข้าสู่ระบบ</a>
                </div>
            </div>
            <div class="footer">
                <p>อีเมลนี้ถูกส่งจากระบบอัตโนมัติ กรุณาอย่าตอบกลับ</p>
                <p>© 2025 TOSTEM Work Process System. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # แนบ HTML content
    html_part = MIMEText(html_content, 'html')
    msg.attach(html_part)
    
    # ส่งอีเมล
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)
            logger.info(f"✅ Email sent successfully to {to_email}")
    except Exception as e:
        logger.error(f"❌ Failed to send email: {e}")
        raise

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    """API สำหรับส่งรหัสผ่านไปยังอีเมล"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip().lower()
        
        # ตรวจสอบว่าเป็นอีเมลที่อนุญาตเท่านั้น
        if username != 'chonthichaphumphung@gmail.com':
            return jsonify({
                'success': False,
                'message': 'ไม่สามารถรีเซ็ตรหัสผ่านสำหรับผู้ใช้นี้ได้'
            }), 403
        
        # ตรวจสอบว่า username มีในระบบหรือไม่
        if username not in USERS:
            return jsonify({
                'success': False,
                'message': 'ไม่พบผู้ใช้ในระบบ'
            }), 404
        
        # ดึงรหัสผ่านจาก USERS dict
        password = USERS[username]
        
        # ส่งอีเมล
        try:
            send_password_email(username, password)
            logger.info(f"Password reset email sent to: {username}")
            
            return jsonify({
                'success': True,
                'message': 'ส่งรหัสผ่านไปยังอีเมลของคุณแล้ว'
            })
            
        except Exception as email_error:
            logger.error(f"Failed to send email: {email_error}")
            return jsonify({
                'success': False,
                'message': f'เกิดข้อผิดพลาดในการส่งอีเมล: {str(email_error)}'
            }), 500
            
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        return jsonify({
            'success': False,
            'message': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500
    
# -------------------- User Management Routes --------------------

@app.route('/user-management')
def user_management_page():
    """หน้าจัดการ User - เฉพาะ Admin เท่านั้น"""
    cleanup_old_files()

    html_template = load_html_template('user-management')
    return render_template_string(html_template)

@app.route('/api/users/list', methods=['GET'])
def list_users():
    """API ดึงรายการ users ทั้งหมด"""
    try:
        users = load_users()
        return jsonify({
            'success': True,
            'users': users,
            'count': len(users)
        })
    except Exception as e:
        logger.error(f"List users error: {e}")
        return jsonify({
            'success': False,
            'error': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500

@app.route('/api/users/add', methods=['POST'])
def add_user():
    """API เพิ่ม user ใหม่"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'user').strip()  # ✅ รับค่า role จาก request
        
        if not username or not password:
            return jsonify({
                'success': False,
                'error': 'กรุณากรอกข้อมูลให้ครบถ้วน'
            }), 400
        
        # โหลด users ปัจจุบัน
        users = load_users()
        
        # ตรวจสอบว่า username ซ้ำหรือไม่
        if username in users:
            return jsonify({
                'success': False,
                'error': 'ชื่อผู้ใช้นี้มีอยู่ในระบบแล้ว'
            }), 400
        
        # ✅ Validate role
        if role not in ['user', 'admin']:
            role = 'user'
        
        # เพิ่ม user ใหม่
        users[username] = {
            'password': password,
            'created_at': datetime.now().isoformat(),
            'role': role  # ✅ ใช้ role ที่ได้รับมาจากฟอร์ม
        }
        
        # บันทึกลงไฟล์
        if save_users(users):
            # อัพเดท USERS dict ใน memory
            global USERS
            USERS = users
            
            logger.info(f"✅ New user added: {username} with role: {role}")
            return jsonify({
                'success': True,
                'message': f'เพิ่มผู้ใช้ "{username}" สำเร็จ (สิทธิ์: {role.upper()})'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'ไม่สามารถบันทึกข้อมูลได้'
            }), 500
            
    except Exception as e:
        logger.error(f"Add user error: {e}")
        return jsonify({
            'success': False,
            'error': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500

@app.route('/api/users/update', methods=['POST'])
def update_user():
    """API แก้ไขข้อมูล user"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        new_password = data.get('password', '').strip()
        new_role = data.get('role', 'user').strip()  # ✅ รับค่า role ด้วย
        
        if not username or not new_password:
            return jsonify({
                'success': False,
                'error': 'กรุณากรอกข้อมูลให้ครบถ้วน'
            }), 400
        
        # โหลด users ปัจจุบัน
        users = load_users()
        
        # ตรวจสอบว่า user มีอยู่จริง
        if username not in users:
            return jsonify({
                'success': False,
                'error': 'ไม่พบผู้ใช้นี้ในระบบ'
            }), 404
        
        # ✅ Validate role
        if new_role not in ['user', 'admin']:
            new_role = 'user'
        
        # อัพเดทรหัสผ่าน และ role
        users[username]['password'] = new_password
        users[username]['role'] = new_role  # ✅ อัพเดท role ด้วย
        users[username]['updated_at'] = datetime.now().isoformat()
        
        # บันทึกลงไฟล์
        if save_users(users):
            # อัพเดท USERS dict ใน memory
            global USERS
            USERS = users
            
            logger.info(f"✅ User updated: {username} with role: {new_role}")
            return jsonify({
                'success': True,
                'message': f'แก้ไขข้อมูล "{username}" สำเร็จ (สิทธิ์: {new_role.upper()})'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'ไม่สามารถบันทึกข้อมูลได้'
            }), 500
            
    except Exception as e:
        logger.error(f"Update user error: {e}")
        return jsonify({
            'success': False,
            'error': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500

@app.route('/api/users/delete', methods=['POST'])
@require_admin  # ✅ ต้องเป็น admin ถึงจะลบได้
def delete_user():
    """API ลบ user"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        
        if not username:
            return jsonify({
                'success': False,
                'error': 'กรุณาระบุชื่อผู้ใช้'
            }), 400
        
        # ✅ ป้องกันไม่ให้ลบตัวเอง
        current_user = request.headers.get('X-Username')
        if username == current_user:
            return jsonify({
                'success': False,
                'error': 'ไม่สามารถลบบัญชีของตัวเองได้'
            }), 400
        
        # โหลด users ปัจจุบัน
        users = load_users()
        
        # ตรวจสอบว่า user มีอยู่จริง
        if username not in users:
            return jsonify({
                'success': False,
                'error': 'ไม่พบผู้ใช้นี้ในระบบ'
            }), 404
        
        # ✅ เตือนถ้าจะลบ admin คนสุดท้าย
        admin_count = sum(1 for u in users.values() if u.get('role') == 'admin')
        if users[username].get('role') == 'admin' and admin_count <= 1:
            return jsonify({
                'success': False,
                'error': 'ไม่สามารถลบ Admin คนสุดท้ายได้'
            }), 400
        
        # ลบ user
        del users[username]
        
        # บันทึกลงไฟล์
        if save_users(users):
            # อัพเดท USERS dict ใน memory
            global USERS
            USERS = users
            
            logger.info(f"✅ User deleted: {username}")
            return jsonify({
                'success': True,
                'message': f'ลบผู้ใช้ "{username}" สำเร็จ'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'ไม่สามารถบันทึกข้อมูลได้'
            }), 500
            
    except Exception as e:
        logger.error(f"Delete user error: {e}")
        return jsonify({
            'success': False,
            'error': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500
    
@app.route('/api/users/check-admin', methods=['GET'])
def check_admin_status():
    """ตรวจสอบว่าผู้ใช้เป็น admin หรือไม่"""
    try:
        username = request.headers.get('X-Username')
        
        if not username:
            return jsonify({
                'success': False,
                'is_admin': False
            })
        
        users = load_users()
        
        if username not in users:
            return jsonify({
                'success': False,
                'is_admin': False
            })
        
        user_role = users[username].get('role', 'user')
        
        return jsonify({
            'success': True,
            'is_admin': user_role == 'admin',
            'username': username,
            'role': user_role
        })
        
    except Exception as e:
        logger.error(f"Check admin status error: {e}")
        return jsonify({
            'success': False,
            'is_admin': False,
            'error': str(e)
        }), 500

@app.route('/matrix')
def index():
    cleanup_old_files()
    html_template = load_html_template('matrix')
    return render_template_string(html_template)

@app.route('/glass-check')
def txt_vs_pdf():
    cleanup_old_files()
    html_template = load_html_template('glass-check')
    return render_template_string(html_template)

@app.route('/joint')
def joint():
    cleanup_old_files()
    html_template = load_html_template('joint')
    return render_template_string(html_template)

@app.route('/text-glass')
def format_page():
    cleanup_old_files()
    html_template = load_html_template('text-glass')
    return render_template_string(html_template)

@app.route('/text-glass2')
def format_page_2():
    """Route for Text Glass Mode 2 (main7.py)"""
    cleanup_old_files()
    if not MAIN7_AVAILABLE:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5;">
        <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
        <h1 style="color: #d32f2f;">Text Glass (2) Not Available</h1>
        <p>The Text Glass (2) feature requires main7.py with PDFExtractorWeb class.</p>
        <p><strong>Current status:</strong></p>
        <ul>
        <li>main7.py: {'✓ Found' if os.path.exists(MAIN7_PATH) else '✗ Missing'}</li>
        <li>text-glass2.html: {'✓ Found' if os.path.exists(BASE_DIR / 'text-glass2.html') else '✗ Missing'}</li>
        <li>PDFExtractorWeb class: {'✓ Available' if MAIN7_AVAILABLE else '✗ Not found'}</li>
        </ul>
        <p><a href="/" style="color: #1976d2; text-decoration: none;">← กลับหน้าหลัก</a></p>
        </div>
        </body></html>
        """)
    
    html_template = load_html_template('text-glass2')
    return render_template_string(html_template)

@app.route('/sitewithele')
def site_with_ele():
    """Route for Site Survey vs ELE comparison"""
    cleanup_old_files()
    if not MAIN5_AVAILABLE:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5;">
        <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
        <h1 style="color: #d32f2f;">Site+ELE Comparison Not Available</h1>
        <p>The Site Survey vs ELE comparison feature requires the following files:</p>
        <h3>Required files:</h3>
        <ul>
        <li><code>main5.py</code> - Main processing script</li>
        <li><code>sub_panel_full.py</code> - Sub-panel processing module</li>
        <li><code>insect_screen_full.py</code> - Insect screen processing module</li>
        <li><code>color_comparison_full.py</code> - Color comparison processing module</li>
        <li><code>door_direction_full.py</code> - Door direction processing module</li>
        <li><code>transom_comparison.py</code> - Transom processing module</li>
        </ul>
        <h3>Installation steps:</h3>
        <ol>
        <li>Ensure all required files are in the same directory as server.py</li>
        <li>Install Python dependencies:
        <pre style="background: #f0f0f0; padding: 10px; border-radius: 5px;">pip install pandas openpyxl pdfplumber python-dotenv openai PyMuPDF</pre>
        </li>
        <li>Set up your .env file with OpenAI API credentials</li>
        </ol>
        <p><strong>Current status:</strong></p>
        <ul>
        <li>main5.py: {'✓ Found' if os.path.exists(MAIN5_PATH) else '✗ Missing'}</li>
        <li>sub_panel_full.py: {'✓ Found' if os.path.exists(BASE_DIR / 'sub_panel_full.py') else '✗ Missing'}</li>
        <li>insect_screen_full.py: {'✓ Found' if os.path.exists(BASE_DIR / 'insect_screen_full.py') else '✗ Missing'}</li>
        <li>color_comparison_full.py: {'✓ Found' if os.path.exists(BASE_DIR / 'color_comparison_full.py') else '✗ Missing'}</li>
        <li>door_direction_full.py: {'✓ Found' if os.path.exists(BASE_DIR / 'door_direction_full.py') else '✗ Missing'}</li>
        <li>transom_comparison.py: {'✓ Found' if os.path.exists(BASE_DIR / 'transom_comparison.py') else '✗ Missing'}</li>
        </ul>
        <p><a href="/" style="color: #1976d2; text-decoration: none;">← กลับหน้าหลัก</a></p>
        </div>
        </body></html>
        """)
    
    html_template = load_html_template('sitewithele')
    return render_template_string(html_template)

@app.route('/Quotation')
def quote_compare_page():
    """Route for Quotation comparison"""
    cleanup_old_files()
    if not MAIN6_AVAILABLE:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5;">
        <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
        <h1 style="color: #d32f2f;">Quotation Comparison Not Available</h1>
        <p>The Quotation comparison feature requires the following files:</p>
        <h3>Required files:</h3>
        <ul>
        <li><code>main6.py</code> - Main quotation processing script with QuoteComparator class</li>
        <li><code>Quotation.html</code> - Frontend interface for quotation comparison</li>
        </ul>
        <h3>Installation steps:</h3>
        <ol>
        <li>Ensure main6.py is in the same directory as server.py</li>
        <li>Install Python dependencies:
        <pre style="background: #f0f0f0; padding: 10px; border-radius: 5px;">pip install pandas pdfplumber flask werkzeug</pre>
        </li>
        <li>Ensure main6.py contains the QuoteComparator class</li>
        </ol>
        <p><strong>Current status:</strong></p>
        <ul>
        <li>main6.py: {'✓ Found' if os.path.exists(MAIN6_PATH) else '✗ Missing'}</li>
        <li>Quotation.html: {'✓ Found' if os.path.exists(BASE_DIR / 'Quotation.html') else '✗ Missing'}</li>
        <li>QuoteComparator class: {'✓ Available' if MAIN6_AVAILABLE else '✗ Not found'}</li>
        </ul>
        <p><a href="/" style="color: #1976d2; text-decoration: none;">← กลับหน้าหลัก</a></p>
        </div>
        </body></html>
        """)
    
    html_template = load_html_template('Quotation')
    return render_template_string(html_template)

@app.route('/Generate')
def generate_page():
    """Route for Site Survey Generator (main8.py)"""
    cleanup_old_files()
    if not MAIN8_AVAILABLE:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5;">
        <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
        <h1 style="color: #d32f2f;">📋 Site Survey Generator Not Available</h1>
        <p>The Site Survey Generator feature requires the following files:</p>
        <h3>Required files:</h3>
        <ul>
        <li><code>main8.py</code> - Main Flask application</li>
        <li><code>Generate.html</code> - Frontend interface</li>
        <li><code>site_survey_generator.py</code> - Core processing module</li>
        <li><code>site_survey_image.py</code> - Image support module</li>
        <li><code>window_door_image_generator.py</code> - Image generation module</li>
        <li><code>site survey.docx</code> - Template file (optional)</li>
        </ul>
        <h3>Installation steps:</h3>
        <ol>
        <li>Ensure all required files are in the same directory as server.py</li>
        <li>Install Python dependencies:
        <pre style="background: #f0f0f0; padding: 10px; border-radius: 5px;">pip install flask flask-cors python-docx reportlab Pillow openpyxl pandas pdfplumber python-dotenv openai</pre>
        </li>
        <li>Create folders: uploads, results, quotations, site_surveys, templates, site_survey_images</li>
        </ol>
        <p><strong>Current status:</strong></p>
        <ul>
        <li>main8.py: {'✓ Found' if os.path.exists(BASE_DIR / 'main8.py') else '✗ Missing'}</li>
        <li>Generate.html: {'✓ Found' if os.path.exists(BASE_DIR / 'Generate.html') else '✗ Missing'}</li>
        <li>site_survey_generator.py: {'✓ Found' if os.path.exists(BASE_DIR / 'site_survey_generator.py') else '✗ Missing'}</li>
        <li>site_survey_image.py: {'✓ Found' if os.path.exists(BASE_DIR / 'site_survey_image.py') else '✗ Missing'}</li>
        <li>window_door_image_generator.py: {'✓ Found' if os.path.exists(BASE_DIR / 'window_door_image_generator.py') else '✗ Missing'}</li>
        </ul>
        <p><a href="/" style="color: #1976d2; text-decoration: none;">← กลับหน้าหลัก</a></p>
        </div>
        </body></html>
        """)
    
    html_template = load_html_template('Generate')
    return render_template_string(html_template)

# -------------------- API Routes --------------------

@app.route('/compare', methods=['POST'])
def compare_files():
    """Handle comparison requests (glass-check functionality)"""
    try:
        text_block = request.form.get("text_block", "")
        pdf_source_file = request.files.get("pdf_source_file")
        pdf_file = request.files.get("pdf_file")
        start_page = int(request.form.get("start_page", 1))
        
        logger.info(f"=== New comparison request ===")
        logger.info(f"Text block length: {len(text_block)}")
        logger.info(f"PDF source file: {pdf_source_file.filename if pdf_source_file else 'None'}")
        logger.info(f"PDF target file: {pdf_file.filename if pdf_file else 'None'}")
        
        has_text_source = text_block and text_block.strip()
        has_pdf_source = pdf_source_file and pdf_source_file.filename
        
        if not has_text_source and not has_pdf_source:
            return jsonify({"error": "ต้องใส่ข้อความหรือเลือกไฟล์ PDF ต้นฉบับ"}), 400
            
        if not pdf_file:
            return jsonify({"error": "ต้องอัปโหลดไฟล์ PDF สำหรับเปรียบเทียบ"}), 400
        
        if not pdf_file.filename.lower().endswith('.pdf'):
            return jsonify({"error": "กรุณาเลือกไฟล์ PDF เท่านั้นสำหรับไฟล์เปรียบเทียบ"}), 400

        pdf_file.seek(0, 2)
        file_size = pdf_file.tell()
        pdf_file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({"error": f"ไฟล์เปรียบเทียบใหญ่เกินไป"}), 400

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = str(uuid.uuid4())[:8]
        job_id = f"{timestamp}_{random_suffix}"
        
        target_filename = secure_filename(pdf_file.filename)
        target_pdf_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_target_{target_filename}')
        
        logger.info(f"Saving target PDF to: {target_pdf_path}")
        pdf_file.save(target_pdf_path)
        
        if not os.path.exists(target_pdf_path):
            return jsonify({"error": "ไม่สามารถบันทึกไฟล์ PDF เปรียบเทียบได้"}), 500

        if has_pdf_source:
            if not pdf_source_file.filename.lower().endswith('.pdf'):
                return jsonify({"error": "กรุณาเลือกไฟล์ PDF เท่านั้นสำหรับไฟล์ต้นฉบับ"}), 400
            
            pdf_source_file.seek(0, 2)
            source_file_size = pdf_source_file.tell()
            pdf_source_file.seek(0)
            
            if source_file_size > MAX_FILE_SIZE:
                return jsonify({"error": f"ไฟล์ต้นฉบับใหญ่เกินไป"}), 400
            
            source_filename = secure_filename(pdf_source_file.filename)
            source_pdf_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_source_{source_filename}')
            
            logger.info(f"Saving source PDF to: {source_pdf_path}")
            pdf_source_file.save(source_pdf_path)
            
            if not os.path.exists(source_pdf_path):
                return jsonify({"error": "ไม่สามารถบันทึกไฟล์ PDF ต้นฉบับได้"}), 500
            
            logger.info(f"Starting PDF vs PDF comparison for job_id: {job_id}")
            result, error = process_comparison_with_main_py('pdf', '', source_pdf_path, target_pdf_path, start_page)
            
        else:
            logger.info(f"Starting Text vs PDF comparison for job_id: {job_id}")
            result, error = process_comparison_with_main_py('text', text_block, '', target_pdf_path, start_page)
        
        if error:
            logger.error(f"Comparison failed: {error}")
            return jsonify({"error": error}), 500

        logger.info(f"Comparison completed successfully for job_id: {job_id}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Unexpected error in compare_files")
        return jsonify({"error": f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}"}), 500

@app.route('/api/process-matrix', methods=['POST'])
def process_matrix_file():
    """Handle matrix mode file processing"""
    try:
        if 'file' not in request.files:
            return jsonify({'message': 'ไม่พบไฟล์'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'message': 'ไม่ได้เลือกไฟล์'}), 400
        if not file.filename.lower().endswith('.xlsx'):
            return jsonify({'message': 'ประเภทไฟล์ไม่ถูกต้อง กรุณาอัปโหลดไฟล์ .xlsx'}), 400

        file_content = file.read()
        if len(file_content) > MAX_FILE_SIZE:
            return jsonify({'message': 'ไฟล์ใหญ่เกินไป (สูงสุด 25MB)'}), 400
        file.seek(0)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = str(uuid.uuid4())[:8]
        job_id = f"{timestamp}_{random_suffix}"

        filename = secure_filename(file.filename)
        input_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_{filename}')
        file.save(input_path)

        logger.info(f"Processing Matrix file: {filename} with job_id: {job_id}")

        if not os.path.exists(BASE_DIR / 'main.py'):
            return jsonify({'message': 'ไม่พบไฟล์ main.py สำหรับ Matrix mode'}), 500

        result, error = process_matrix_file_with_main_py(input_path, job_id, file.filename)
        if error:
            return jsonify({'message': error}), 500

        logger.info(f"Matrix processing completed successfully for job_id: {job_id}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Unexpected error in matrix processing")
        return jsonify({'message': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'}), 500

@app.route('/api/process-joint', methods=['POST'])
def process_joint_file():
    """Handle joint mode file processing - UPDATED"""
    try:
        if 'file' not in request.files:
            return jsonify({'message': 'ไม่พบไฟล์'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'message': 'ไม่ได้เลือกไฟล์'}), 400
        if not file.filename.lower().endswith('.xlsx'):
            return jsonify({'message': 'ประเภทไฟล์ไม่ถูกต้อง กรุณาอัปโหลดไฟล์ .xlsx'}), 400

        file_content = file.read()
        if len(file_content) > MAX_FILE_SIZE:
            return jsonify({'message': 'ไฟล์ใหญ่เกินไป (สูงสุด 25MB)'}), 400
        file.seek(0)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = str(uuid.uuid4())[:8]
        job_id = f"{timestamp}_{random_suffix}"

        filename = secure_filename(file.filename)
        input_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_{filename}')
        file.save(input_path)

        logger.info(f"Processing Joint file: {filename} with job_id: {job_id}")

        # Check available scripts
        main2_exists = os.path.exists(BASE_DIR / 'main2.py')
        main_exists = os.path.exists(BASE_DIR / 'main.py')
        
        if not main2_exists and not main_exists:
            return jsonify({'message': 'ไม่พบไฟล์ main2.py หรือ main.py สำหรับ Joint mode'}), 500

        result, error = process_joint_file_with_main_py(input_path, job_id)
        if error:
            # If the error mentions "unrecognized arguments: --mode", provide specific guidance
            if "--mode" in error and "unrecognized arguments" in error:
                error_msg = f'ไฟล์ main.py ไม่รองรับ Joint mode โดยตรง กรุณาใช้ไฟล์ main2.py สำหรับ Joint processing หรืออัพเดต main.py ให้รองรับ --mode joint'
            else:
                error_msg = error
            return jsonify({'message': error_msg}), 500

        logger.info(f"Joint processing completed successfully for job_id: {job_id}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Unexpected error in joint processing")
        return jsonify({'message': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'}), 500

@app.route('/upload', methods=['POST'])
def upload_pdf_or_site_ele():
    """Handle PDF upload for text-glass mode OR site+ele uploads"""
    try:
        # Check if this is a Site+ELE comparison request
        if 'site_survey' in request.files and 'ele_file' in request.files:
            return handle_site_ele_upload()
        
        if 'site_survey' in request.files and 'file' in request.files and 'ele_file' not in request.files:
            return handle_main7_upload()
        
        # Otherwise handle PDF upload for text-glass mode
        if 'file' not in request.files:
            return jsonify({'error': 'ไม่พบไฟล์'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'ไม่ได้เลือกไฟล์'}), 400
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'กรุณาเลือกไฟล์ PDF เท่านั้น'}), 400

        file_content = file.read()
        if len(file_content) > MAX_FILE_SIZE:
            return jsonify({'error': 'ไฟล์ใหญ่เกินไป (สูงสุด 25MB)'}), 400
        file.seek(0)

        start_page = int(request.form.get('start_page', 3))

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = str(uuid.uuid4())[:8]
        job_id = f"{timestamp}_{random_suffix}"

        filename = secure_filename(file.filename)
        input_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_{filename}')
        file.save(input_path)

        logger.info(f"Processing PDF file: {filename} with job_id: {job_id}, start_page: {start_page}")

        if not os.path.exists(BASE_DIR / 'main.py') and not os.path.exists(BASE_DIR / 'main3.py'):
            return jsonify({'error': 'ไม่พบไฟล์ main.py หรือ main3.py สำหรับ Format mode'}), 500

        result, error = process_pdf_file_with_main_py(input_path, start_page, job_id)
        if error:
            return jsonify({'error': error}), 500

        logger.info(f"PDF processing completed successfully for job_id: {job_id}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Unexpected error in PDF processing")
        return jsonify({'error': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'}), 500

def handle_site_ele_upload():
    """Handle Site Survey + ELE file upload and processing"""
    if not MAIN5_AVAILABLE:
        return jsonify({'error': 'Site+ELE comparison not available. Please check main5.py and dependencies.'}), 400
    
    site_file = request.files['site_survey']
    ele_file = request.files['ele_file']
    
    if site_file.filename == '' or ele_file.filename == '':
        return jsonify({'error': 'กรุณาเลือกไฟล์ทั้งสองไฟล์'}), 400
    
    # Validate file types
    if not (site_file.filename.lower().endswith('.pdf') and ele_file.filename.lower().endswith('.pdf')):
        return jsonify({'error': 'กรุณาเลือกไฟล์ PDF เท่านั้น'}), 400
    
    # Save files securely
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = str(uuid.uuid4())[:8]
    
    site_filename = f"{timestamp}_{random_suffix}_site_{secure_filename(site_file.filename)}"
    ele_filename = f"{timestamp}_{random_suffix}_ele_{secure_filename(ele_file.filename)}"
    
    site_path = os.path.join(UPLOAD_FOLDER, site_filename)
    ele_path = os.path.join(UPLOAD_FOLDER, ele_filename)
    
    site_file.save(site_path)
    ele_file.save(ele_path)
    
    logger.info(f"Files saved: {site_filename}, {ele_filename}")
    
    # Process the comparison using direct import
    result, error = process_site_ele_direct_import(site_path, ele_path)
    
    # Clean up uploaded files
    try:
        os.remove(site_path)
        os.remove(ele_path)
    except Exception as e:
        logger.warning(f"Could not remove temporary files: {e}")
    
    if error:
        return jsonify({'error': error}), 500
    
    return jsonify(result)

@app.route('/api/process-quotation', methods=['POST'])
def process_quotation_file():
    """Handle quotation comparison file processing via direct import"""
    if not MAIN6_AVAILABLE:
        return jsonify({'error': 'Quotation comparison not available. Please check main6.py and dependencies.'}), 400
    
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({'error': 'กรุณาอัปโหลดไฟล์ PDF 2 ไฟล์'}), 400

        file1 = request.files['file1']
        file2 = request.files['file2']

        if file1.filename == '' or file2.filename == '':
            return jsonify({'error': 'ไม่ได้เลือกไฟล์'}), 400

        # Validate file types
        if not (file1.filename.lower().endswith('.pdf') and file2.filename.lower().endswith('.pdf')):
            return jsonify({'error': 'กรุณาเลือกไฟล์ PDF เท่านั้น'}), 400

        # Check file sizes
        file1_content = file1.read()
        file2_content = file2.read()
        
        if len(file1_content) > MAX_FILE_SIZE or len(file2_content) > MAX_FILE_SIZE:
            return jsonify({'error': 'ไฟล์ใหญ่เกินไป (สูงสุด 25MB)'}), 400
        
        file1.seek(0)
        file2.seek(0)

        # Save files securely
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = str(uuid.uuid4())[:8]
        job_id = f"{timestamp}_{random_suffix}"

        file1_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_1_{secure_filename(file1.filename)}')
        file2_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_2_{secure_filename(file2.filename)}')
        
        file1.save(file1_path)
        file2.save(file2_path)

        logger.info(f"Processing quotation comparison with job_id: {job_id}")
        logger.info(f"File1: {file1.filename}, File2: {file2.filename}")

        # Process the comparison using direct import
        result, error = process_quotation_comparison_direct_import(file1_path, file2_path)

        # Clean up uploaded files
        try:
            os.remove(file1_path)
            os.remove(file2_path)
        except Exception as e:
            logger.warning(f"Could not remove temporary files: {e}")

        if error:
            logger.error(f"Quotation comparison failed: {error}")
            return jsonify({'error': error}), 500

        logger.info(f"Quotation comparison completed successfully for job_id: {job_id}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Error in process_quotation_file")
        return jsonify({'error': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'}), 500

@app.route('/download/<format>')
def download_pdf_results(format: str):
    """Download PDF processing results"""
    try:
        if format == 'txt':
            txt_file = os.path.join(OUTPUT_FOLDER, 'pdf_results.txt')
            if not os.path.exists(txt_file):
                return jsonify({'error': 'ไม่พบไฟล์ผลลัพธ์'}), 404
            return send_file(txt_file, as_attachment=True, download_name='pdf_extraction_results.txt')
        elif format == 'json':
            json_file = os.path.join(OUTPUT_FOLDER, 'pdf_results.json')
            if not os.path.exists(json_file):
                return jsonify({'error': 'ไม่พบไฟล์ผลลัพธ์'}), 404
            return send_file(json_file, as_attachment=True, download_name='pdf_extraction_results.json')
        else:
            return jsonify({'error': 'รูปแบบไฟล์ไม่ถูกต้อง'}), 400
    except Exception as e:
        return jsonify({'error': f'เกิดข้อผิดพลาดในการดาวน์โหลด: {str(e)}'}), 500

@app.route('/api/download/<job_id>/<file_type>')
def download_file(job_id: str, file_type: str):
    """Download matrix/joint processing results"""
    try:
        if file_type == 'price':
            filename = f'Price_{job_id}.xlsx'
        elif file_type == 'type':
            filename = f'Type_{job_id}.xlsx'
        else:
            return jsonify({'message': 'ประเภทไฟล์ไม่ถูกต้อง'}), 400

        file_path = os.path.join(OUTPUT_FOLDER, filename)
        if not os.path.exists(file_path):
            return jsonify({'message': 'ไม่พบไฟล์'}), 404

        download_name = 'Price.xlsx' if file_type == 'price' else 'Type.xlsx'
        return send_file(
            file_path,
            as_attachment=True,
            download_name=download_name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"Download error: {e}")
        return jsonify({'message': f'เกิดข้อผิดพลาดในการดาวน์โหลด: {str(e)}'}), 500

# -------------------- Download Excel Route --------------------
@app.route('/download_excel', methods=['POST'])
def download_excel():
    """Download Excel file for Site+ELE comparison results"""
    try:
        import pandas as pd
        import io
        
        data = request.json
        comparison_results = data.get('results', [])
        site_data = data.get('site_data', [])
        ele_data = data.get('ele_data', [])
        combined_comparison = data.get('combined_comparison', [])
        insect_screen_results = data.get('insect_screen_results', [])
        color_results = data.get('color_results', [])
        direction_results = data.get('direction_results', [])
        transom_results = data.get('transom_results', [])
        
        # Create Excel file in memory
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Combined comparison sheet (main table)
            if combined_comparison:
                df_combined = pd.DataFrame(combined_comparison)
                df_combined.to_excel(writer, sheet_name='Combined_Comparison', index=False)
            
            # Main comparison sheet
            if comparison_results:
                df_comparison = pd.DataFrame(comparison_results)
                df_comparison.to_excel(writer, sheet_name='Main_Comparison', index=False)
            
            # Site data sheet
            if site_data:
                df_site = pd.DataFrame(site_data)
                df_site.to_excel(writer, sheet_name='Site_Survey', index=False)
            
            # ELE data sheet
            if ele_data:
                df_ele = pd.DataFrame(ele_data)
                df_ele.to_excel(writer, sheet_name='ELE_Data', index=False)
            
            if insect_screen_results:
                df_insect_screen = pd.DataFrame(insect_screen_results)
                df_insect_screen.to_excel(writer, sheet_name='Insect_Screen_Details', index=False)
            
            if color_results:
                df_color = pd.DataFrame(color_results)
                df_color.to_excel(writer, sheet_name='Color_Details', index=False)

            if direction_results:
                df_direction = pd.DataFrame(direction_results)
                df_direction.to_excel(writer, sheet_name='Door_Direction_Details', index=False)

            if transom_results:
                df_transom = pd.DataFrame(transom_results)
                df_transom.to_excel(writer, sheet_name='Transom_Details', index=False)
        
        output.seek(0)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Combined_Analysis_with_Colors_{timestamp}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Error creating Excel file: {e}")
        return jsonify({'error': f'เกิดข้อผิดพลาดในการสร้างไฟล์ Excel: {str(e)}'}), 500


# ===== ตั้งชื่อไฟล์ Site Survey ตามชื่อไฟล์ Quotation =====
# เช่น Quotation "คุณภาธร-Quo2026070804.pdf"
#      -> site_survey_คุณภาธร-Quo2026070804.docx / .pdf

# อักขระที่ใช้ในชื่อไฟล์ไม่ได้ (Windows/macOS/Linux) - ตัวอักษรไทยใช้ได้ปกติ
_INVALID_FILENAME_CHARS = r'\/:*?"<>|'


def sanitize_filename_keep_unicode(name: str, max_length: int = 120) -> str:
    """ทำความสะอาดชื่อไฟล์แต่ยังเก็บภาษาไทยไว้ (ต่างจาก secure_filename ที่ตัดทิ้ง)"""
    name = os.path.basename(name or '')

    cleaned = ''.join(
        ('_' if (ch in _INVALID_FILENAME_CHARS or ord(ch) < 32) else ch)
        for ch in name
    )

    # ยุบช่องว่างซ้ำ และตัดจุด/ช่องว่างหัวท้าย
    cleaned = ' '.join(cleaned.split()).strip(' .')

    return cleaned[:max_length]


def build_site_survey_basename(quotation_job: dict) -> str:
    """สร้างชื่อไฟล์ (ไม่รวมนามสกุล) จากชื่อไฟล์ Quotation ต้นฉบับ"""
    quotation_job = quotation_job or {}

    source_name = (
        quotation_job.get('display_filename')
        or quotation_job.get('original_filename')
        or ''
    )

    stem = sanitize_filename_keep_unicode(os.path.splitext(source_name)[0])

    if not stem:
        # ไม่มีชื่อให้ใช้ - fallback เป็น timestamp
        stem = datetime.now().strftime('%Y%m%d_%H%M%S')

    return f'site_survey_{stem}'


def unique_output_basename(output_dir: str, base_name: str) -> str:
    """กันไฟล์เดิมถูกเขียนทับ - ถ้ามีชื่อซ้ำจะเติม _2, _3, ..."""
    candidate = base_name
    counter = 2

    while any(
        os.path.exists(os.path.join(output_dir, f'{candidate}{ext}'))
        for ext in ('.docx', '.pdf')
    ):
        candidate = f'{base_name}_{counter}'
        counter += 1

    return candidate


if MAIN8_AVAILABLE:
    
    @app.route('/api/health', methods=['GET'])
    def health_check_site_survey():
        """Health check for Site Survey Generator"""
        try:
            template_exists = os.path.exists('site survey.docx')
            
            return jsonify({
                'status': 'healthy',
                'site_survey_support': True,
                'template_available': template_exists,
                'template_path': 'site survey.docx' if template_exists else None,
                'docx_available': DOCX_AVAILABLE,
                'pdf_support': REPORTLAB_AVAILABLE,
                'reportlab_available': REPORTLAB_AVAILABLE,
                'smart_mosquito_detection': True,
                'features': {
                    'auto_mosquito_merge': True,
                    'smart_ref_grouping': True,
                    'insect_screen_detection': True
                },
                'message': 'ระบบพร้อมใช้งาน (พร้อม Smart Mosquito Detection)'
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'เกิดข้อผิดพลาด: {str(e)}'
            }), 500
    
    @app.route('/api/quotation/upload', methods=['POST'])
    def upload_quotation():
        """Upload quotation file for processing"""
        try:
            # โหลด users เพื่อ refresh ข้อมูล
            global USERS
            USERS = load_users()
            
            # ตรวจสอบว่ามีไฟล์หรือไม่
            if 'file' not in request.files:
                logger.error("❌ No file in request.files")
                logger.error(f"Request.files keys: {list(request.files.keys())}")
                return jsonify({'success': False, 'error': 'ไม่พบไฟล์ในการส่ง'}), 400
            
            file = request.files['file']
            
            if file.filename == '':
                logger.error("❌ Empty filename")
                return jsonify({'success': False, 'error': 'ไม่ได้เลือกไฟล์'}), 400
            
            # ตรวจสอบนามสกุลไฟล์
            allowed_extensions = ('.xlsx', '.xls', '.csv', '.pdf')
            if not file.filename.lower().endswith(allowed_extensions):
                logger.error(f"❌ Invalid file type: {file.filename}")
                return jsonify({
                    'success': False, 
                    'error': f'ประเภทไฟล์ไม่ได้รับอนุญาต กรุณาใช้: {", ".join(allowed_extensions)}'
                }), 400
            
            # ดึง start_page
            start_page = int(request.form.get('start_page', 1))
            
            # สร้าง job_id
            job_id = str(uuid.uuid4())
            filename = secure_filename(file.filename)
            
            # สร้างโฟลเดอร์ถ้ายังไม่มี
            os.makedirs(QUOTATION_FOLDER, exist_ok=True)
            
            file_path = os.path.join(QUOTATION_FOLDER, f"{job_id}_{filename}")
            
            logger.info(f"📤 Saving file to: {file_path}")
            file.save(file_path)
            
            # ตรวจสอบว่าไฟล์ถูกบันทึกจริง
            if not os.path.exists(file_path):
                logger.error(f"❌ File not saved: {file_path}")
                return jsonify({
                    'success': False,
                    'error': 'ไม่สามารถบันทึกไฟล์ได้'
                }), 500
            
            file_size = os.path.getsize(file_path)
            logger.info(f"✅ File saved successfully: {filename} ({file_size} bytes)")
            
            # ประมวลผลไฟล์
            logger.info(f"🔄 Processing quotation file: {filename}")
            result = process_quotation_file2(file_path, start_page=start_page)
            
            # เก็บข้อมูล job
            quotation_jobs[job_id] = {
                'status': 'completed' if result['success'] else 'error',
                'original_filename': filename,
                # ✅ ชื่อไฟล์ต้นฉบับ (ยังมีภาษาไทย) - ใช้ตั้งชื่อไฟล์ Site Survey
                'display_filename': os.path.basename(file.filename or ''),
                'file_path': file_path,
                'processed_data': result.get('data', {}),
                'message': result['message'],
                'processed_at': datetime.now().isoformat(),
                'start_page': start_page
            }
            
            logger.info(f"✅ Quotation processed successfully: {job_id}")
            
            return jsonify({
                'success': result['success'],
                'job_id': job_id,
                'message': result['message'],
                'start_page': start_page,
                'products_count': len(result.get('data', {}).get('products', []))
            })
            
        except Exception as e:
            logger.error(f"❌ Error in upload_quotation: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }), 500
    
    @app.route('/api/quotation/refs/<job_id>', methods=['GET'])
    def get_quotation_refs(job_id):
        """Get ref codes from quotation job"""
        try:
            if job_id not in quotation_jobs:
                return jsonify({
                    'success': False,
                    'error': 'ไม่พบ job ที่ระบุ'
                }), 404
            
            job = quotation_jobs[job_id]
            
            if job.get('status') != 'completed':
                return jsonify({
                    'success': False,
                    'error': 'งานยังไม่เสร็จสิ้น'
                }), 400
            
            products = job.get('processed_data', {}).get('products', [])
            refs = [p.get('ref') for p in products if p.get('ref')]
            
            return jsonify({
                'success': True,
                'job_id': job_id,
                'refs': refs,
                'count': len(refs)
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/site-survey/upload-image', methods=['POST'])
    def upload_site_survey_image():
        """Upload site survey image with ref matching"""
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'ไม่พบไฟล์'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'ไม่ได้เลือกไฟล์'}), 400
        
        try:
            import re
            
            ref_from_form = request.form.get('ref', '').strip().upper()
            filename = secure_filename(file.filename)
            
            # ✅ IMPROVED: More flexible ref extraction
            def extract_ref_from_filename(filename):
                name_without_ext = os.path.splitext(filename)[0]
                
                # ✅ ชื่อไฟล์กล้อง ไม่ใช่ ref - ปล่อยให้เป็น UNMATCHED
                if re.match(r'^(DSC|DCIM|PXL|PANO|VID|MOV|SCR|IMG)[\d_\-]',
                            name_without_ext, re.IGNORECASE):
                    return None

                # Try multiple patterns with increasing flexibility
                # ✅ รองรับ prefix อื่นนอกจาก D/W (AD1, ADD1, SD1)
                #    เดิม [DW][A-Z]? ทำให้ AD1.jpg ถูกอ่านเป็น "D1" (ผิด ref เงียบๆ)
                #    ใช้ lookaround แทน \b เพราะ "_" นับเป็น word char
                #    ทำให้ \b ไม่ทำงานกับ AD1_front.jpg
                REF = r'[A-Z]{1,3}\d+(?:\.\d+)?(?:[FT]\d*)?'
                EDGE_L = r'(?<![A-Za-z0-9])'
                EDGE_R = r'(?![A-Za-z0-9])'
                patterns = [
                    # Exact match at start
                    rf'^({REF}){EDGE_R}',
                    # Exact match at end
                    rf'{EDGE_L}({REF})$',
                    # With separators
                    rf'[_\-\s]({REF})[_\-\s]',
                    # Anywhere in filename (ต้องขึ้นต้นคำ)
                    rf'{EDGE_L}({REF}){EDGE_R}',
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, name_without_ext, re.IGNORECASE)
                    if match:
                        return match.group(1).upper()
                
                return None
            
            ref_from_filename = extract_ref_from_filename(filename)
            final_ref = ref_from_form or ref_from_filename
            
            # ✅ Add detailed logging
            logger.info(f"📸 Image upload attempt:")
            logger.info(f"   Original filename: {filename}")
            logger.info(f"   Ref from form: {ref_from_form}")
            logger.info(f"   Ref from filename: {ref_from_filename}")
            logger.info(f"   Final ref: {final_ref}")
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            file_ext = os.path.splitext(filename)[1].lower()
            
            # ✅ Validate image extension
            allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
            if file_ext not in allowed_extensions:
                return jsonify({
                    'success': False,
                    'error': f'ไม่รองรับไฟล์ประเภท {file_ext}'
                }), 400
            
            unique_filename = f"{final_ref or 'NOREF'}_{timestamp}_{uuid.uuid4().hex[:8]}{file_ext}"
            
            # ✅ Ensure directory exists
            os.makedirs(SITE_SURVEY_IMAGES_FOLDER, exist_ok=True)
            
            file_path = os.path.join(SITE_SURVEY_IMAGES_FOLDER, unique_filename)
            file.save(file_path)
            
            absolute_path = os.path.abspath(file_path)
            
            if not os.path.exists(absolute_path):
                logger.error(f"❌ File save failed: {absolute_path}")
                return jsonify({
                    'success': False,
                    'error': 'ไม่สามารถบันทึกไฟล์ได้'
                }), 500
            
            image_data = {
                'filename': filename,
                'stored_filename': unique_filename,
                'path': absolute_path,
                'relative_path': f"{SITE_SURVEY_IMAGES_FOLDER}/{unique_filename}",
                'ref': final_ref,
                'uploaded_at': datetime.now().isoformat(),
                'size': os.path.getsize(absolute_path)
            }
            
            logger.info(f"✅ Saved image: {unique_filename} at {absolute_path}")
            
            # Store in database
            if final_ref:
                if final_ref not in uploaded_images_db:
                    uploaded_images_db[final_ref] = []
                uploaded_images_db[final_ref].append(image_data)
                logger.info(f"✅ Mapped to ref: {final_ref}")
            else:
                if 'UNMATCHED' not in uploaded_images_db:
                    uploaded_images_db['UNMATCHED'] = []
                uploaded_images_db['UNMATCHED'].append(image_data)
                logger.warning(f"⚠️ No ref match - stored as UNMATCHED")
            
            return jsonify({
                'success': True,
                'message': f'อัปโหลดสำเร็จ: {filename}',
                'ref': final_ref,
                'filename': unique_filename,
                'file_path': image_data['relative_path'],
                'matched': bool(final_ref)
            })
            
        except Exception as e:
            logger.error(f"❌ Upload error: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }), 500
    
    @app.route('/api/site-survey/list-images', methods=['GET'])
    def list_all_images():
        """List all uploaded images grouped by ref"""
        try:
            result = {}
            total_count = 0
            
            for ref, images in uploaded_images_db.items():
                result[ref] = {
                    'count': len(images),
                    'images': [
                        {
                            'filename': img['filename'],
                            'stored_filename': img['stored_filename'],
                            'path': img['relative_path'],
                            'uploaded_at': img['uploaded_at'],
                            'size': img['size']
                        }
                        for img in images
                    ]
                }
                total_count += len(images)
            
            return jsonify({
                'success': True,
                'total_images': total_count,
                'refs_count': len(result),
                'images_by_ref': result
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/site-survey/generate', methods=['POST'])
    def generate_site_survey():
        """Generate site survey report"""
        data = request.get_json()
        quotation_job_id = data.get('quotation_job_id')
        
        if not quotation_job_id:
            return jsonify({'error': 'ต้องมีข้อมูล Quotation อย่างน้อยหนึ่งอย่าง'}), 400
        
        try:
            survey_job_id = str(uuid.uuid4())
            
            site_survey_jobs[survey_job_id] = {
                'status': 'processing',
                'quotation_job_id': quotation_job_id,
                'message': 'กำลังสร้างรายงาน...',
                'generated_at': datetime.now().isoformat()
            }
            
            quo_data = {}
            quotation_job = quotation_jobs.get(quotation_job_id, {})
            if quotation_job:
                quo_data = quotation_job.get('processed_data', {})

            # ✅ ตั้งชื่อไฟล์ผลลัพธ์ตามชื่อไฟล์ Quotation
            #    เช่น site_survey_คุณภาธร-Quo2026070804.docx / .pdf
            os.makedirs(SITE_SURVEY_FOLDER, exist_ok=True)
            output_basename = unique_output_basename(
                SITE_SURVEY_FOLDER,
                build_site_survey_basename(quotation_job)
            )
            logger.info(f"📝 Site survey output basename: {output_basename}")

            template_path = 'site survey.docx'
            if not os.path.exists(template_path):
                template_path = None
            
            images_by_ref = {}
            if uploaded_images_db:
                for ref, images in uploaded_images_db.items():
                    valid_images = []
                    
                    for img in images:
                        img_path = img.get('path')
                        
                        if not img_path:
                            logger.warning(f"Image has no path: {img.get('filename')}")
                            continue
                        
                        abs_path = os.path.abspath(img_path)
                        
                        if not os.path.exists(abs_path):
                            logger.warning(f"Image file not found: {abs_path}")
                            continue

                        valid_images.append({
                            'filename': img['filename'],
                            'path': abs_path,
                            'relative_path': img.get('relative_path', '')
                        })
                        
                        logger.info(f"Valid image: {img['filename']} at {abs_path}")
                    
                    if valid_images:
                        images_by_ref[ref] = valid_images
                        logger.info(f"Ref {ref}: {len(valid_images)} valid images")

            result = generate_site_survey_report(
                quo_data=quo_data,
                output_dir=SITE_SURVEY_FOLDER,
                template_path=template_path,
                images_by_ref=images_by_ref,
                output_basename=output_basename
            )

            files_info = {}
            if result.get('success') and result.get('files'):
                for file_type, file_result in result['files'].items():
                    if file_result.get('success') and file_result.get('file_path'):
                        files_info[file_type] = {
                            'file_path': file_result['file_path'],
                            'success': file_result['success'],
                            'message': file_result.get('message', ''),
                            'size': os.path.getsize(file_result['file_path']) if os.path.exists(file_result['file_path']) else 0
                        }
            
            site_survey_jobs[survey_job_id].update({
                'status': 'completed' if result['success'] else 'error',
                'output_basename': output_basename,
                'files': result.get('files', {}),
                'file_paths': {
                    'docx': result.get('files', {}).get('docx', {}).get('file_path'),
                    'pdf': result.get('files', {}).get('pdf', {}).get('file_path')
                },
                'merged_data': result.get('merged_data', {}),
                'message': result['message']
            })
            
            logger.info(f"Survey job completed: {survey_job_id}, files: {list(files_info.keys())}")
            
            return jsonify({
                'success': result['success'],
                'survey_job_id': survey_job_id,
                'message': result['message'],
                'files_generated': list(files_info.keys()),
                'products_processed': len(result.get('merged_data', {}).get('products', []))
            })
            
        except Exception as e:
            logger.error(f"Error generating site survey: {str(e)}", exc_info=True)
            
            if 'survey_job_id' in locals():
                site_survey_jobs[survey_job_id].update({
                    'status': 'error',
                    'message': f'เกิดข้อผิดพลาด: {str(e)}',
                    'error_details': str(e)
                })
            
            return jsonify({
                'success': False,
                'message': f'เกิดข้อผิดพลาดในการสร้างรายงาน: {str(e)}'
            }), 500
    
    @app.route('/api/site-survey/download/<survey_job_id>/<file_type>', methods=['GET'])
    def download_site_survey(survey_job_id, file_type):
        """Download site survey report - ✅ AUTO FALLBACK TO DOCX"""
        try:
            logger.info(f"📥 Download request: {survey_job_id}, type: {file_type}")
            
            if survey_job_id not in site_survey_jobs:
                return jsonify({'error': f'ไม่พบงาน Survey ID: {survey_job_id}'}), 404
            
            job = site_survey_jobs[survey_job_id]
            
            if job.get('status') != 'completed':
                return jsonify({'error': f'งานยังไม่เสร็จสิ้น - สถานะ: {job.get("status")}'}), 400

            # ✅ ชื่อไฟล์ตอนดาวน์โหลด เช่น site_survey_คุณภาธร-Quo2026070804.pdf
            def download_name_for(path, fallback_type):
                ext = os.path.splitext(path)[1].lstrip('.') or fallback_type
                base_name = job.get('output_basename')
                if base_name:
                    return f'{base_name}.{ext}'
                return os.path.basename(path)

            # ✅ ลำดับการหาไฟล์: PDF → DOCX
            file_types_to_try = [file_type]
            if file_type == 'pdf':
                file_types_to_try.append('docx')  # ✅ fallback เป็น DOCX

            for try_type in file_types_to_try:
                # หาจาก job info
                job_files = job.get('files', {})
                if try_type in job_files:
                    file_info = job_files[try_type]
                    file_path = file_info.get('file_path')
                    
                    if file_path and os.path.exists(file_path):
                        file_size = os.path.getsize(file_path)
                        
                        if file_size > 1000:  # ไฟล์ปกติ
                            logger.info(f"✅ Sending {try_type.upper()}: {file_path} ({file_size} bytes)")

                            return send_file(
                                file_path,
                                as_attachment=True,
                                download_name=download_name_for(file_path, try_type),
                                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document' if try_type == 'docx' else 'application/pdf'
                            )

                # หาในโฟลเดอร์
                import glob
                search_patterns = [
                    glob.escape(f'{job.get("output_basename") or survey_job_id}') + f'.{try_type}',
                    f'*{survey_job_id}*.{try_type}',
                    f'enhanced_site_survey_*.{try_type}',
                    f'site_survey_*.{try_type}'
                ]
                
                for pattern in search_patterns:
                    found_files = glob.glob(os.path.join(SITE_SURVEY_FOLDER, pattern))
                    
                    if found_files:
                        found_files.sort(key=os.path.getctime, reverse=True)
                        file_path = found_files[0]
                        file_size = os.path.getsize(file_path)
                        
                        if file_size > 1000:
                            logger.info(f"✅ Found {try_type.upper()}: {file_path} ({file_size} bytes)")
                            
                            return send_file(
                                file_path,
                                as_attachment=True,
                                download_name=download_name_for(file_path, try_type),
                                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document' if try_type == 'docx' else 'application/pdf'
                            )

            # ❌ ไม่เจอเลย
            return jsonify({
                'error': f'ไม่พบไฟล์ {file_type.upper()} หรือ DOCX',
                'job_status': job.get('status'),
                'suggestion': 'ลองสร้างรายงานใหม่อีกครั้ง'
            }), 404
            
        except Exception as e:
            logger.error(f"Download error: {str(e)}", exc_info=True)
            return jsonify({'error': f'เกิดข้อผิดพลาด: {str(e)}'}), 500
    
    @app.route('/api/jobs/list', methods=['GET'])
    def list_jobs():
        """List all jobs"""
        return jsonify({
            'quotation_jobs': {
                k: {
                    'status': v['status'],
                    'original_filename': v.get('original_filename', ''),
                    # ✅ ชื่อไฟล์ต้นฉบับ (ภาษาไทยไม่หาย) สำหรับแสดงบนหน้าเว็บ
                    'display_filename': v.get('display_filename') or v.get('original_filename', '')
                }
                for k, v in quotation_jobs.items()
            },
            'site_survey_jobs': {k: {'status': v['status'], 'message': v['message']} for k, v in site_survey_jobs.items()}
        })
    
    @app.route('/api/cleanup/<job_id>', methods=['DELETE'])
    def cleanup_job(job_id):
        """Clean up job data and files"""
        try:
            cleaned = False
            
            if job_id in quotation_jobs:
                job = quotation_jobs[job_id]
                file_path = job.get('file_path')
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                
                quotation_jobs.pop(job_id, None)
                cleaned = True
            
            if job_id in site_survey_jobs:
                job = site_survey_jobs[job_id]
                files = job.get('files', {})
                
                for file_info in files.values():
                    file_path = file_info.get('file_path')
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                
                site_survey_jobs.pop(job_id, None)
                cleaned = True
            
            if cleaned:
                return jsonify({'success': True, 'message': 'ลบข้อมูลเรียบร้อย'})
            else:
                return jsonify({'error': 'ไม่พบงานที่ระบุ'}), 404
            
        except Exception as e:
            return jsonify({'error': f'เกิดข้อผิดพลาดในการลบข้อมูล: {str(e)}'}), 500
    
    @app.route('/api/cleanup-all', methods=['DELETE', 'POST'])
    def cleanup_all_jobs():
        """Clean up all jobs on page refresh"""
        try:
            cleaned_items = {
                'quotation_jobs': 0,
                'site_survey_jobs': 0,
                'quotation_files': 0,
                'survey_files': 0,
                'uploaded_images': 0
            }
            
            quotation_job_ids = list(quotation_jobs.keys())
            for job_id in quotation_job_ids:
                job = quotation_jobs[job_id]
                file_path = job.get('file_path')
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    cleaned_items['quotation_files'] += 1
                
                cleaned_items['quotation_jobs'] += 1
            
            quotation_jobs.clear()
            
            survey_job_ids = list(site_survey_jobs.keys())
            for job_id in survey_job_ids:
                job = site_survey_jobs[job_id]
                files = job.get('files', {})
                
                for file_info in files.values():
                    file_path = file_info.get('file_path')
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                        cleaned_items['survey_files'] += 1
                
                cleaned_items['site_survey_jobs'] += 1
            
            site_survey_jobs.clear()

            image_count = sum(len(images) for images in uploaded_images_db.values())

            for ref, images in uploaded_images_db.items():
                for img in images:
                    img_path = img.get('path')
                    if img_path and os.path.exists(img_path):
                        try:
                            os.remove(img_path)
                            cleaned_items['uploaded_images'] += 1
                        except:
                            pass

            uploaded_images_db.clear()
            
            try:
                for item in os.listdir(QUOTATION_FOLDER):
                    item_path = os.path.join(QUOTATION_FOLDER, item)
                    try:
                        os.remove(item_path)
                    except:
                        pass
                
                for item in os.listdir(SITE_SURVEY_FOLDER):
                    item_path = os.path.join(SITE_SURVEY_FOLDER, item)
                    try:
                        os.remove(item_path)
                    except:
                        pass

                for item in os.listdir(SITE_SURVEY_IMAGES_FOLDER):
                    item_path = os.path.join(SITE_SURVEY_IMAGES_FOLDER, item)
                    try:
                        os.remove(item_path)
                    except:
                        pass
                        
            except Exception as cleanup_error:
                logger.warning(f"Warning during folder cleanup: {cleanup_error}")
            
            return jsonify({
                'success': True,
                'message': 'ลบข้อมูลทั้งหมดสำเร็จ',
                'cleaned_items': cleaned_items
            })
            
        except Exception as e:
            logger.error(f"Error in cleanup_all_jobs: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'เกิดข้อผิดพลาดในการลบข้อมูล: {str(e)}'
            }), 500

@app.route('/health')
def health_check():
    """Enhanced health check showing all scripts and templates"""
    # Check all main scripts
    scripts = {
        'main.py': os.path.exists(BASE_DIR / 'main.py'),
        'main2.py': os.path.exists(BASE_DIR / 'main2.py'),
        'main3.py': os.path.exists(BASE_DIR / 'main3.py'),
        'main4.py': os.path.exists(BASE_DIR / 'main4.py'),
        'main5.py': os.path.exists(MAIN5_PATH),
        'main6.py': os.path.exists(MAIN6_PATH),
        'main7.py': os.path.exists(MAIN7_PATH),
        'main8.py': os.path.exists(MAIN8_PATH),
    }
    
    # Check all HTML templates
    templates = {
        'Excel-Matrix.html': os.path.exists(BASE_DIR / 'Excel-Matrix.html'),
        'Excel_Joint.html': os.path.exists(BASE_DIR / 'Excel_Joint.html'),
        'text-glass.html': os.path.exists(BASE_DIR / 'text-glass.html'),
        'glass-check.html': os.path.exists(BASE_DIR / 'glass-check.html'),
        'site-with-ele.html': os.path.exists(BASE_DIR / 'site-with-ele.html'),
        'Quotation.html': os.path.exists(BASE_DIR / 'Quotation.html'),
        'text-glass2.html': os.path.exists(BASE_DIR / 'text-glass2.html'),
        'Generate.html': os.path.exists(BASE_DIR / 'Generate.html'),
    }

    main8_dependencies = {}
    if scripts['main8.py']:
        deps = ['site_survey_generator.py', 'site_survey_image.py', 'window_door_image_generator.py']
        for dep in deps:
            main8_dependencies[dep] = os.path.exists(BASE_DIR / dep)
    
    # Test main.py execution
    main_py_test = None
    try:
        if scripts['main.py']:
            cmd = [PYTHON, str(BASE_DIR / 'main.py'), '--help']
            result = run_subprocess(cmd)
            main_py_test = {
                'return_code': result.returncode,
                'can_execute': result.returncode in [0, 2],  # 0 = success, 2 = argument error (but script runs)
                'has_output': bool(result.stdout.strip()),
                'has_error': bool(result.stderr.strip())
            }
    except Exception as e:
        main_py_test = {'error': str(e)}
    
    # Check dependencies for main5.py
    main5_dependencies = {}
    if scripts['main5.py']:
        deps = ['sub_panel_full.py', 'insect_screen_full.py', 'color_comparison_full.py', 'door_direction_full.py', 'transom_comparison.py']
        for dep in deps:
            main5_dependencies[dep] = os.path.exists(BASE_DIR / dep)
    
    # Count available features
    available_features = []
    if scripts['main.py']:
        available_features.extend(['matrix', 'joint', 'text-glass'])
    if scripts['main4.py'] or scripts['main.py']:
        available_features.append('glass-check')
    if MAIN5_AVAILABLE:
        available_features.append('sitewithele')
    if MAIN6_AVAILABLE:
        available_features.append('Quotation')
    if MAIN7_AVAILABLE:
        available_features.append('text-glass2')
    if MAIN8_AVAILABLE:
        available_features.append('Generate')
    
    return jsonify({
        'status': 'healthy',
        'current_directory': str(BASE_DIR),
        'python_executable': PYTHON,
        'available_scripts': scripts,
        'available_templates': templates,
        'main_py_test': main_py_test,
        'user_management': {
            'enabled': True,
            'users_file': str(USERS_FILE),
            'users_file_exists': os.path.exists(USERS_FILE),
            'total_users': len(load_users())
        },
        'main5_integration': {
            'available': MAIN5_AVAILABLE,
            'script_exists': scripts['main5.py'],
            'dependencies': main5_dependencies,
            'can_execute': MAIN5_AVAILABLE
        },
        'main6_integration': {
            'available': MAIN6_AVAILABLE,
            'script_exists': scripts['main6.py'],
            'can_execute': MAIN6_AVAILABLE
        },
        'main7_integration': {
            'available': MAIN7_AVAILABLE,
            'script_exists': scripts['main7.py'],
            'can_execute': MAIN7_AVAILABLE
        },
        'main8_integration': {
            'available': MAIN8_AVAILABLE,
            'script_exists': scripts['main8.py'],
            'dependencies': main8_dependencies,
            'can_execute': MAIN8_AVAILABLE
        },
        'folders': {
            'uploads': os.path.exists(UPLOAD_FOLDER),
            'outputs': os.path.exists(OUTPUT_FOLDER),
            'quotations': os.path.exists(QUOTATION_FOLDER),
            'site_surveys': os.path.exists(SITE_SURVEY_FOLDER),
            'site_survey_images': os.path.exists(SITE_SURVEY_IMAGES_FOLDER)
        },
        'supported_features': available_features,
        'feature_status': {
            'matrix': 'available' if scripts['main.py'] else 'missing main.py',
            'joint': 'available' if scripts['main.py'] else 'missing main.py', 
            'text-glass': 'available' if scripts['main.py'] or scripts['main3.py'] else 'missing scripts',
            'glass-check': 'available' if scripts['main4.py'] or scripts['main.py'] else 'missing scripts',
            'sitewithele': 'available' if MAIN5_AVAILABLE else 'disabled - check main5.py and dependencies',
            'Quotation': 'available' if MAIN6_AVAILABLE else 'disabled - check main6.py and dependencies',
            'text-glass2': 'available' if MAIN7_AVAILABLE else 'disabled - check main7.py',
            'Generate': 'available' if MAIN8_AVAILABLE else 'disabled - check main8.py'
        },
        'routes_map': {
            '/': 'Matrix Mode (Excel-Matrix.html)',
            '/matrix': 'Matrix Mode (Excel-Matrix.html)',
            '/joint': 'Joint Mode (Excel_Joint.html)',
            '/text-glass': 'Text Glass Mode (text-glass.html)',
            '/glass-check': 'Glass Check Mode (glass-check.html)',
            '/sitewithele': 'Site+ELE Comparison (site-with-ele.html)',
            '/Quotation': 'Quotation Comparison (Quotation.html)',
            '/text-glass2': 'Text Glass Mode 2 (text-glass2.html)',
            '/Generate': 'Site Survey Generator (Generate.html)',
            '/health': 'Health Check'
        }
    })

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'ไฟล์ใหญ่เกินไป (สูงสุด 25MB)'}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'ไม่พบหน้าที่ต้องการ'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error")
    return jsonify({'error': 'เกิดข้อผิดพลาดภายในเซิร์ฟเวอร์'}), 500

# -------------------- Run --------------------
if __name__ == '__main__':
    print("🚀 Starting PDF/TXT Quotation Comparator Server...")
    print(f"📁 Base directory: {BASE_DIR}")
    print(f"📁 Upload folder: {UPLOAD_FOLDER}")
    print(f"📁 Output folder: {OUTPUT_FOLDER}")
    print(f"🐍 Python executable: {PYTHON}")
    print(f"🔗 Main5 available: {'✅ Yes' if MAIN5_AVAILABLE else '❌ No'}")
    print(f"🔗 Main6 available: {'✅ Yes' if MAIN6_AVAILABLE else '❌ No'}")
    print()
    print("🌐 Available routes:")
    print("   http://localhost:5000/          → Matrix Mode (Excel-Matrix.html)")
    print("   http://localhost:5000/matrix    → Matrix Mode (Excel-Matrix.html)")
    print("   http://localhost:5000/joint     → Joint Mode (Excel_Joint.html)")
    print("   http://localhost:5000/text-glass → Text Glass Mode (text-glass.html)")
    print("   http://localhost:5000/glass-check → Glass Check Mode (glass-check.html)")
    print("   http://localhost:5000/sitewithele → Site+ELE Comparison (site-with-ele.html)" + (" ✅" if MAIN5_AVAILABLE else " ❌"))
    print("   http://localhost:5000/Quotation → Quotation Comparison (Quotation.html)" + (" ✅" if MAIN6_AVAILABLE else " ❌"))
    print("   http://localhost:5000/text-glass2 → Text Glass 2" + (" ✅" if MAIN7_AVAILABLE else " ❌"))
    print("   http://localhost:5000/Generate  → Site Survey Generator" + (" ✅" if MAIN8_AVAILABLE else " ❌"))
    print("   http://localhost:5000/health    → Health Check")
    print(f"🔗 Main7 available: {'✅ Yes' if MAIN7_AVAILABLE else '❌ No'}")
    print("   http://localhost:5000/text-glass2 → Text Glass Mode 2 (text-glass2.html)" + (" ✅" if MAIN7_AVAILABLE else " ❌"))
    print()

    # Check all scripts
    all_scripts = ['main.py', 'main2.py', 'main3.py', 'main4.py', 'main5.py', 'main6.py','main7.py']
    for script in all_scripts:
        file_path = BASE_DIR / script
        if os.path.exists(file_path):
            print(f"✅ {script} found")
        else:
            print(f"❌ {script} NOT FOUND")

    print()
    
    # Check all templates
    all_templates = ['Excel-Matrix.html', 'Excel_Joint.html', 'text-glass.html', 'glass-check.html', 'site-with-ele.html', 'Quotation.html']
    for template in all_templates:
        file_path = BASE_DIR / template
        if os.path.exists(file_path):
            print(f"✅ {template} found")
        else:
            print(f"❌ {template} NOT FOUND")

    if not MAIN5_AVAILABLE and os.path.exists(MAIN5_PATH):
        print()
        print("⚠️  main5.py exists but cannot be executed.")
        print("   This might be due to missing dependencies or import errors.")
        print("   Check that these files exist:")
        print("   - sub_panel_full.py")
        print("   - insect_screen_full.py") 
        print("   - color_comparison_full.py")
        print("   - door_direction_full.py")
        print("   - transom_comparison.py")
        print("   And install: pip install pandas openpyxl pdfplumber python-dotenv openai PyMuPDF")

    if not MAIN6_AVAILABLE and os.path.exists(MAIN6_PATH):
        print()
        print("⚠️  main6.py exists but cannot be executed.")
        print("   This might be due to missing dependencies or missing QuoteComparator class.")
        print("   Check that main6.py contains the QuoteComparator class")
        print("   And install: pip install pandas pdfplumber flask werkzeug")

    print()
    print("📋 Supported modes:")
    print("   • matrix: Process Excel files in matrix format (main.py)")
    print("   • joint: Process Excel files in joint format (main.py)")
    print("   • text-glass: Extract and format PDF content (main.py/main3.py)")
    print("   • glass-check: Compare text/PDF against PDF content (main.py/main4.py)")
    
    if MAIN5_AVAILABLE:
        print("   • sitewithele: Site Survey vs ELE comparison ✅ (main5.py)")
    else:
        print("   • sitewithele: (disabled - main5.py issues) ❌")
        
    if MAIN6_AVAILABLE:
        print("   • Quotation: Compare two quotation PDFs ✅ (main6.py)")
    else:
        print("   • Quotation: (disabled - main6.py issues) ❌")

    if MAIN8_AVAILABLE:
        print("📋 Site Survey Generator Features:")
        print("   • Smart Mosquito Detection")
        print("   • Auto Ref Grouping")
        print("   • Image Upload & Management")
        print("   • DOCX & PDF Export")
        print()
    
    print()
    print("Press Ctrl+C to stop the server")
    print("-" * 50)

    app.run(debug=True, host='0.0.0.0', port=5000)