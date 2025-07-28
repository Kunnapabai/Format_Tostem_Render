from flask import Flask, request, jsonify, send_file, render_template_string
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

# -------------------- Config & Globals --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB
ALLOWED_EXTENSIONS = {'xlsx', 'pdf'}

BASE_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable                  # ใช้ python ของ .venv แน่นอน

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

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
        'matrix': 'index.html',
        'joint': 'index2.html',
        'text-glass': 'index3.html',
        'glass-check': 'index4.html'  # TXT vs PDF Checker ใช้ index4.html
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
    env["PYTHONNOUSERSITE"] = "1"  # กันไม่ให้ไปดึง package จาก user-site
    
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

# -------------------- Comparison Processing --------------------
def process_comparison_with_main_py(source_type: str, source_data: str, source_pdf_path: str, target_pdf_path: str, start_page: int = 1):
    """Process comparison using main.py with different modes"""
    try:
        start_time = time.time()

        # ตรวจสอบว่า main.py มีอยู่หรือไม่
        main_py_path = BASE_DIR / 'main.py'
        logger.info(f"main.py path: {main_py_path}")
        logger.info(f"main.py exists: {os.path.exists(main_py_path)}")

        if not os.path.exists(main_py_path):
            return None, f'ไม่พบไฟล์ main.py ที่ {main_py_path}'

        if source_type == 'text':
            # Text vs PDF mode - ใช้ main4.py หรือ main.py แบบใหม่ถ้ามี
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
                # ลองใช้ main.py แบบใหม่
                logger.info(f"Processing text vs PDF comparison with main.py (new format)")
                cmd = [
                    PYTHON, str(main_py_path),
                    '--mode', 'text_vs_pdf',
                    '--text', source_data,
                    '--target-pdf', target_pdf_path,
                    '--target-start-page', str(start_page)
                ]
        elif source_type == 'pdf':
            # PDF vs PDF mode - ใช้ main4.py หรือ main.py แบบใหม่ถ้ามี
            main4_py_path = BASE_DIR / 'main4.py'
            if os.path.exists(main4_py_path):
                logger.info(f"Processing PDF vs PDF comparison with main4.py")
                cmd = [
                    PYTHON, str(main4_py_path),
                    '--mode', 'pdf_vs_pdf',
                    '--source-pdf', source_pdf_path,
                    '--target-pdf', target_pdf_path,
                    '--source-start-page', '3',  # Default for structured PDF
                    '--target-start-page', str(start_page)
                ]
            else:
                # ลองใช้ main.py แบบใหม่
                logger.info(f"Processing PDF vs PDF comparison with main.py (new format)")
                cmd = [
                    PYTHON, str(main_py_path),
                    '--mode', 'pdf_vs_pdf',
                    '--source-pdf', source_pdf_path,
                    '--target-pdf', target_pdf_path,
                    '--source-start-page', '3',  # Default for structured PDF
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

# -------------------- Matrix Mode --------------------
def process_matrix_file_with_main_py(input_path: str, job_id: str, original_filename: str | None):
    try:
        start_time = time.time()

        # ลองใช้ main.py แบบใหม่ก่อน (ถ้ารองรับ --mode)
        cmd_new = [
            PYTHON, str(BASE_DIR / 'main.py'),
            '--mode', 'matrix',
            '--input', input_path,
            '--job-id', job_id,
            '--output-dir', OUTPUT_FOLDER
        ]
        if original_filename:
            cmd_new += ['--original-filename', original_filename]

        # ลองใช้ main.py แบบเดิม (legacy format)
        cmd_legacy = [
            PYTHON, str(BASE_DIR / 'main.py'),
            '--input', input_path,
            '--job-id', job_id,
            '--output-dir', OUTPUT_FOLDER
        ]
        if original_filename:
            cmd_legacy += ['--original-filename', original_filename]

        # ลองใช้ command แบบใหม่ก่อน
        result = run_subprocess(cmd_new)
        
        # ถ้าใช้ไม่ได้ (return code 2 = argument error) ลองใช้แบบเดิม
        if result.returncode == 2 and '--mode' in ' '.join(cmd_new):
            logger.info("New format failed, trying legacy format...")
            result = run_subprocess(cmd_legacy)

        processing_time = time.time() - start_time

        # Clean input
        try:
            os.remove(input_path)
        except Exception:
            pass

        if result.returncode != 0:
            logger.error("Processing failed with main.py: %s", result.stderr)
            return None, f'เกิดข้อผิดพลาดในการประมวลผล: {result.stderr}'

        # หา JSON จาก stdout
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

# -------------------- Joint Mode --------------------
def process_joint_file_with_main_py(input_path: str, job_id: str):
    try:
        start_time = time.time()

        cmd = [
            PYTHON, str(BASE_DIR / 'main.py'),
            '--mode', 'joint',
            '--input', input_path,
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
            logger.error("Processing failed with main.py: %s", result.stderr)
            return None, f'เกิดข้อผิดพลาดในการประมวลผล: {result.stderr}'

        # Parse JSON output
        output_lines = result.stdout.strip().split('\n')
        json_output = None
        
        # Try to find JSON output
        for line in reversed(output_lines):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    json_output = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass

        if json_output:
            # New JSON format
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
            # Legacy format for backward compatibility
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
                    price_count = int(line.split(':', 1)[1])
                elif line.startswith('TYPE_COUNT:'):
                    type_count = int(line.split(':', 1)[1])

            if price_file and os.path.exists(price_file):
                shutil.move(price_file, os.path.join(OUTPUT_FOLDER, f'Price_{job_id}.xlsx'))
            if type_file and os.path.exists(type_file):
                shutil.move(type_file, os.path.join(OUTPUT_FOLDER, f'Type_{job_id}.xlsx'))

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
        logger.exception("Unexpected error with main.py")
        return None, f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'

# -------------------- PDF Format Mode --------------------
def process_pdf_file_with_main_py(input_path: str, start_page: int, job_id: str):
    try:
        start_time = time.time()

        # ใช้ main3.py ถ้ามี (ไม่เปลี่ยนแปลง)
        main3_py_path = BASE_DIR / 'main3.py'
        if os.path.exists(main3_py_path):
            logger.info(f"Processing PDF file with main3.py")
            cmd = [PYTHON, str(main3_py_path), input_path, str(start_page), job_id]
        else:
            # ใช้ main.py แบบใหม่
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

# -------------------- Routes --------------------
@app.route('/')
@app.route('/matrix')
def index():
    cleanup_old_files()
    html_template = load_html_template('matrix')  # ใช้ index.html สำหรับ Matrix Mode
    return render_template_string(html_template)

@app.route('/glass-check')
def txt_vs_pdf():
    cleanup_old_files()
    html_template = load_html_template('glass-check')  # ใช้ index4.html สำหรับ TXT vs PDF
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

# -------------------- Compare Route --------------------
@app.route('/compare', methods=['POST'])
def compare_files():
    try:
        # ดึงข้อมูลจาก request
        text_block = request.form.get("text_block", "")
        pdf_source_file = request.files.get("pdf_source_file")
        pdf_file = request.files.get("pdf_file")
        start_page = int(request.form.get("start_page", 1))
        
        # Debug logging
        logger.info(f"=== New comparison request ===")
        logger.info(f"Text block length: {len(text_block)}")
        logger.info(f"PDF source file: {pdf_source_file.filename if pdf_source_file else 'None'}")
        logger.info(f"PDF target file: {pdf_file.filename if pdf_file else 'None'}")
        
        # ตรวจสอบ source data
        has_text_source = text_block and text_block.strip()
        has_pdf_source = pdf_source_file and pdf_source_file.filename
        
        if not has_text_source and not has_pdf_source:
            return jsonify({"error": "ต้องใส่ข้อความหรือเลือกไฟล์ PDF ต้นฉบับ"}), 400
            
        if not pdf_file:
            return jsonify({"error": "ต้องอัปโหลดไฟล์ PDF สำหรับเปรียบเทียบ"}), 400
        
        if not pdf_file.filename.lower().endswith('.pdf'):
            return jsonify({"error": "กรุณาเลือกไฟล์ PDF เท่านั้นสำหรับไฟล์เปรียบเทียบ"}), 400

        # ตรวจสอบขนาดไฟล์ target
        pdf_file.seek(0, 2)
        file_size = pdf_file.tell()
        pdf_file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({"error": f"ไฟล์เปรียบเทียบใหญ่เกินไป (ได้รับ {file_size} bytes, สูงสุด {MAX_FILE_SIZE} bytes)"}), 400

        # สร้างไฟล์ชื่อชั่วคราว
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = str(uuid.uuid4())[:8]
        job_id = f"{timestamp}_{random_suffix}"
        
        # บันทึกไฟล์ target PDF
        target_filename = secure_filename(pdf_file.filename)
        target_pdf_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_target_{target_filename}')
        
        logger.info(f"Saving target PDF to: {target_pdf_path}")
        pdf_file.save(target_pdf_path)
        
        # ตรวจสอบว่าไฟล์ถูกบันทึกแล้ว
        if not os.path.exists(target_pdf_path):
            return jsonify({"error": "ไม่สามารถบันทึกไฟล์ PDF เปรียบเทียบได้"}), 500

        # จัดการ source data และประมวลผล
        if has_pdf_source:
            # PDF vs PDF mode
            if not pdf_source_file.filename.lower().endswith('.pdf'):
                return jsonify({"error": "กรุณาเลือกไฟล์ PDF เท่านั้นสำหรับไฟล์ต้นฉบับ"}), 400
            
            # ตรวจสอบขนาดไฟล์ source
            pdf_source_file.seek(0, 2)
            source_file_size = pdf_source_file.tell()
            pdf_source_file.seek(0)
            
            if source_file_size > MAX_FILE_SIZE:
                return jsonify({"error": f"ไฟล์ต้นฉบับใหญ่เกินไป (ได้รับ {source_file_size} bytes, สูงสุด {MAX_FILE_SIZE} bytes)"}), 400
            
            # บันทึกไฟล์ source PDF
            source_filename = secure_filename(pdf_source_file.filename)
            source_pdf_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_source_{source_filename}')
            
            logger.info(f"Saving source PDF to: {source_pdf_path}")
            pdf_source_file.save(source_pdf_path)
            
            # ตรวจสอบว่าไฟล์ถูกบันทึกแล้ว
            if not os.path.exists(source_pdf_path):
                return jsonify({"error": "ไม่สามารถบันทึกไฟล์ PDF ต้นฉบับได้"}), 500
            
            # ประมวลผลด้วย main.py (PDF vs PDF mode)
            logger.info(f"Starting PDF vs PDF comparison for job_id: {job_id}")
            result, error = process_comparison_with_main_py('pdf', '', source_pdf_path, target_pdf_path, start_page)
            
        else:
            # Text vs PDF mode
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
    try:
        if 'file' not in request.files:
            return jsonify({'message': 'ไม่พบไฟล์'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'message': 'ไม่ได้เลือกไฟล์'}), 400
        if not file.filename.lower().endswith('.xlsx'):
            return jsonify({'message': 'ประเภทไฟล์ไม่ถูกต้อง กรุณาอัพโหลดไฟล์ .xlsx'}), 400

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
    try:
        if 'file' not in request.files:
            return jsonify({'message': 'ไม่พบไฟล์'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'message': 'ไม่ได้เลือกไฟล์'}), 400
        if not file.filename.lower().endswith('.xlsx'):
            return jsonify({'message': 'ประเภทไฟล์ไม่ถูกต้อง กรุณาอัพโหลดไฟล์ .xlsx'}), 400

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

        if not os.path.exists(BASE_DIR / 'main.py'):
            return jsonify({'message': 'ไม่พบไฟล์ main.py สำหรับ Joint mode'}), 500

        result, error = process_joint_file_with_main_py(input_path, job_id)
        if error:
            return jsonify({'message': error}), 500

        logger.info(f"Joint processing completed successfully for job_id: {job_id}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Unexpected error in joint processing")
        return jsonify({'message': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'}), 500

@app.route('/upload', methods=['POST'])
def upload_pdf():
    try:
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

        if not os.path.exists(BASE_DIR / 'main.py'):
            return jsonify({'error': 'ไม่พบไฟล์ main.py สำหรับ Format mode'}), 500

        result, error = process_pdf_file_with_main_py(input_path, start_page, job_id)
        if error:
            return jsonify({'error': error}), 500

        logger.info(f"PDF processing completed successfully for job_id: {job_id}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Unexpected error in PDF processing")
        return jsonify({'error': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}'}), 500

@app.route('/download/<format>')
def download_pdf_results(format: str):
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

@app.route('/health')
def health_check():
    main_py_exists = os.path.exists(BASE_DIR / 'main.py')
    index_html_exists = os.path.exists(BASE_DIR / 'index.html')
    
    # ทดสอบเรียก main.py
    test_result = None
    try:
        if main_py_exists:
            cmd = [PYTHON, str(BASE_DIR / 'main.py'), '--help']
            result = run_subprocess(cmd)
            test_result = {
                'return_code': result.returncode,
                'has_output': bool(result.stdout.strip()),
                'has_error': bool(result.stderr.strip())
            }
    except Exception as e:
        test_result = {'error': str(e)}
    
    return jsonify({
        'status': 'healthy',
        'current_directory': str(BASE_DIR),
        'python_executable': PYTHON,
        'available_scripts': {
            'main.py': main_py_exists,
        },
        'available_templates': {
            'index.html': index_html_exists,
        },
        'main_py_test': test_result,
        'folders': {
            'uploads': os.path.exists(UPLOAD_FOLDER),
            'outputs': os.path.exists(OUTPUT_FOLDER)
        },
        'supported_modes': [
            'text_vs_pdf',
            'pdf_vs_pdf', 
            'matrix',
            'joint',
            'format'
        ]
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
    print()
    print("🌐 Available routes:")
    print("   http://localhost:5000/          → Matrix Mode")
    print("   http://localhost:5000/matrix    → Matrix Mode")
    print("   http://localhost:5000/glass-check → TXT/PDF vs PDF Checker")
    print("   http://localhost:5000/joint     → Joint Mode")
    print("   http://localhost:5000/text-glass    → Format Mode - PDF Processing")
    print("   http://localhost:5000/health    → Health Check")
    print()

    required_files = ['main.py', 'index.html']
    for f in required_files:
        file_path = BASE_DIR / f
        if os.path.exists(file_path):
            print(f"✅ {f} found at {file_path}")
        else:
            print(f"❌ {f} NOT FOUND at {file_path}")

    optional_files = ['main2.py', 'main3.py', 'main4.py', 'index2.html', 'index3.html', 'index4.html']
    for f in optional_files:
        file_path = BASE_DIR / f
        if os.path.exists(file_path):
            print(f"✅ {f} found at {file_path}")
        else:
            print(f"⚠️  {f} not found (will use main.py or fallback)")

    print()
    print("🔍 Testing dependencies...")
    
    try:
        import flask
        print("✅ Flask is installed")
    except ImportError:
        print("❌ Flask not installed")

    try:
        import pdfplumber
        print("✅ pdfplumber is installed")
    except ImportError:
        print("⚠️  pdfplumber not installed - trying PyPDF2...")
        try:
            import PyPDF2
            print("✅ PyPDF2 is installed")
        except ImportError:
            print("❌ No PDF library installed")
            print("   Install with: pip install pdfplumber")

    try:
        import pandas
        import openpyxl
        print("✅ Excel processing libraries (pandas, openpyxl) are installed")
    except ImportError:
        print("⚠️  Excel processing libraries not installed")
        print("   Install with: pip install pandas openpyxl")

    print()
    print("📋 Supported modes:")
    print("   • text_vs_pdf: Compare text against PDF content")
    print("   • pdf_vs_pdf: Compare two PDF files")
    print("   • matrix: Process Excel files in matrix format")
    print("   • joint: Process Excel files in joint format")
    print("   • format: Extract and format PDF content")
    print()
    print("⚠️  Press Ctrl+C to stop the server")
    print("-" * 50)

    app.run(debug=True, host='0.0.0.0', port=5000)