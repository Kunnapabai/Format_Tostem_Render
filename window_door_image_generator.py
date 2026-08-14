#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# use of site_survey_generator.py

"""
window_door_image_generator.py
ระบบสร้างรูปภาพบานประตู/หน้าต่างพร้อม dimensions โดยใช้ AI
พร้อมระบบ matching product type กับรูปต้นแบบ
อัพเดท: รองรับ L/R และสร้างรูป 2 แบบพร้อมกัน
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import base64
from io import BytesIO

# ============================================================
# Composed (multi-panel) drawings
# ------------------------------------------------------------
# บาน 1 ชุดอาจประกอบจากหลาย product ใน quotation เช่น
#     D4.0   Awning window   650 x 1400
#     D4.0F  Fixed window    650 x 700
# ต่อกันเป็นบานเดียว 650 x 2100 (Awning อยู่บน, Fixed อยู่ล่าง)
#
# กติกา:
#   - ความกว้างเท่ากัน  -> ต่อกันแนวตั้ง  เรียงตาม quotation "บนลงล่าง"
#   - ความสูงเท่ากัน    -> ต่อกันแนวนอน  เรียงตาม quotation "ซ้ายไปขวา"
#   - สัดส่วนของแต่ละบานวาดตามขนาดจริง (1400:700 = 2:1)
#   - รอยต่อเป็นเส้นคู่ (แต่ละรูปมีเฟรมของตัวเอง) ตรงตามสินค้าจริง
# ============================================================

# ระยะคลาดเคลื่อนที่ยังถือว่า "ขนาดเท่ากัน"
DIM_TOLERANCE_MM = 20
DIM_TOLERANCE_PCT = 0.02

# ความละเอียดของรูปประกอบ (ด้านยาว)
# docx ย่อรูปให้พอดีกรอบ 3.5 นิ้วอยู่แล้ว ค่านี้จึงมีผลกับความคมชัดเท่านั้น
COMPOSED_LONG_SIDE_PX = 900
COMPOSED_MIN_SIDE_PX = 60

# ฟอนต์ที่ใช้กับรูปประกอบ (arial.ttf ไม่มีบน macOS/Linux)
# ⚠️ ต้องรองรับ Linux ด้วย เพราะ production (Render) เป็น Linux
_FONT_CANDIDATES = {
    'regular': [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "arial.ttf",
    ],
    'bold': [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "arialbd.ttf",
    ],
}


def _load_font(size: int, bold: bool = False):
    """
    โหลดฟอนต์แบบ scale ได้

    ถ้าไม่เจอไฟล์ฟอนต์เลย (เช่นบนเซิร์ฟเวอร์ที่ไม่ได้ติดตั้งฟอนต์)
    ต้องใช้ load_default(size=...) ซึ่ง scale ได้ ไม่ใช่ load_default()
    ที่เป็น bitmap 8px ตายตัว มิฉะนั้นตัวเลข dimension จะเล็กจนอ่านไม่ออก
    """
    for path in _FONT_CANDIDATES['bold' if bold else 'regular']:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue

    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _dims_match(a: float, b: float) -> bool:
    """ขนาด 2 ค่าถือว่าเท่ากันหรือไม่ (เผื่อ tolerance)"""
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) <= max(DIM_TOLERANCE_MM, DIM_TOLERANCE_PCT * max(a, b))


def get_panel_segments(product: Dict) -> List[Dict]:
    """ดึงรายการบานย่อยของ product เรียงตามลำดับแถวใน quotation"""
    segments = product.get('panel_segments') or []
    valid = [
        s for s in segments
        if (s.get('width') or 0) > 0 and (s.get('height') or 0) > 0
    ]
    return sorted(valid, key=lambda s: s.get('_row_index', 0))


def determine_layout(segments: List[Dict], ref: str = "") -> Tuple[str, int, int]:
    """
    ตัดสินใจว่าบานย่อยต่อกันแนวไหน และคืนขนาดรวม

    Returns:
        (direction, total_width, total_height)
        direction = 'vertical' (ต่อบนลงล่าง) หรือ 'horizontal' (ต่อซ้ายไปขวา)
    """
    widths = [int(s.get('width') or 0) for s in segments]
    heights = [int(s.get('height') or 0) for s in segments]

    same_width = all(_dims_match(widths[0], w) for w in widths[1:])
    same_height = all(_dims_match(heights[0], h) for h in heights[1:])

    if same_width and not same_height:
        direction = 'vertical'
    elif same_height and not same_width:
        direction = 'horizontal'
    elif same_width and same_height:
        # บานขนาดเท่ากันทุกด้าน - เดาไม่ได้ ใช้ต่อแนวตั้งเป็นค่าเริ่มต้น
        direction = 'vertical'
        print(f"  ℹ️  {ref}: segments identical in both dimensions, defaulting to vertical stack")
    else:
        # ขนาดไม่ตรงกันทั้งกว้างและสูง = ต่อกันแบบตาราง (ยังไม่รองรับ)
        direction = 'vertical'
        print(f"  ⚠️  {ref}: mixed grid layout not supported yet "
              f"(W={widths}, H={heights}) - falling back to vertical stack")

    if direction == 'vertical':
        return direction, max(widths), sum(heights)
    return direction, sum(widths), max(heights)


class WindowDoorImageGenerator:
    """ระบบสร้างรูปภาพบานประตู/หน้าต่างอัจฉริยะ"""
    
    def __init__(self, base_photo_dir: str = "TOSTEM Drawing", 
                 openai_api_key: str = None,
                 openai_base_url: str = None):
        """
        Args:
            base_photo_dir: โฟล์เดอร์หลักที่เก็บรูปต้นแบบ (ไม่มีโฟล์เดอร์ย่อย)
            openai_api_key: API key สำหรับ OpenAI
            openai_base_url: Base URL สำหรับ OpenAI API
        """
        self.base_photo_dir = Path(base_photo_dir)
        self.output_dir = Path("generated_panel_images")
        self.output_dir.mkdir(exist_ok=True)
        
        # Pattern matching สำหรับ product type
        self.type_patterns = self._build_type_patterns()
        
    def _build_type_patterns(self) -> Dict[str, Dict]:
        """สร้าง patterns สำหรับ matching product type กับรูป"""
        return {
            # Windows
            'fix': {
                'keywords': ['fix', 'fixed', 'ฟิกซ์'],
                'priority': 1
            },
            'casement': {
                'keywords': ['casement', 'เคสเมนท์', 'บานสวิง', 'swing'],
                'priority': 1
            },
            'awning': {
                'keywords': ['awning', 'อ๊อนนิ่ง', 'บานกระทุ้ง'],
                'priority': 1
            },
            'airflow': {
                'keywords': ['airflow', 'air flow', 'แอร์โฟลว์', 'ระบายอากาศ'],
                'priority': 1
            },
            'sliding_window': {
                'keywords': ['sliding window', 'หน้าต่างบานเลื่อน', 'slide window'],
                'priority': 2
            },
            
            # Doors
            'hinged_door': {
                'keywords': ['hinged', 'บานสวิง door', 'swing door'],
                'priority': 1
            },
            'sliding_door': {
                'keywords': ['sliding door', 'ประตูบานเลื่อน', 'slide door'],
                'priority': 2
            },
            'folding': {
                'keywords': ['folding', 'บานพับ', 'fold'],
                'priority': 1
            },
            
            # Special types
            'louver': {
                'keywords': ['louver', 'ลูเวอร์', 'บานเกล็ด'],
                'priority': 3
            },
            'projection': {
                'keywords': ['projection', 'โปรเจค'],
                'priority': 3
            }
        }

    def match_product_type_to_image(self, product_type: str, ref: str = "", type2: str = "") -> Optional[Path]:
        """
        หารูปที่ตรงกับ product type จากโฟลเดอร์ TOSTEM Drawing
        รองรับ combo type (มี +) เช่น "Awning window + Fixed window"
        
        Args:
            product_type: ประเภทสินค้า เช่น "Casement window (2)", "Sliding door 3P3T"
            ref: รหัสอ้างอิง เช่น "W1", "D2"
            type2: ประเภทที่สอง (สำหรับ combo type) เช่น "Fixed window"
        
        Returns:
            Path ของรูปที่เจอ หรือ None ถ้าไม่เจอ
        """
        if not product_type:
            return None

        product_type_lower = product_type.lower()
        print(f"🔍 Matching: '{product_type}' (Ref: {ref}, Type2: {type2})")

        if not self.base_photo_dir.exists():
            print(f"  ❌ Folder not found: {self.base_photo_dir}")
            return None

        print(f"  📁 Searching in: {self.base_photo_dir}")

        # ========== ตรวจสอบ Combo Type (มี +) ==========
        if '+' in product_type:
            print(f"  🔗 Detected combo type with '+'")
            
            # แยก type หลักออกจาก product_type_main (เอาเฉพาะคำแรก)
            main_type = product_type_lower.split('+')[0].strip()
            
            # ถ้ามี type2 ให้ใช้ type2, ถ้าไม่มีให้ใช้ส่วนหลัง +
            if type2:
                second_type = type2.lower().strip()
            else:
                parts = product_type_lower.split('+')
                second_type = parts[1].strip() if len(parts) > 1 else ""
            
            # ลบคำว่า "window" หรือ "door" ออกเพื่อให้ได้เฉพาะ type
            main_type = re.sub(r'\s*(window|door)\s*', '', main_type).strip()
            second_type = re.sub(r'\s*(window|door)\s*', '', second_type).strip()
            
            print(f"     Main type: '{main_type}'")
            print(f"     Second type: '{second_type}'")
            
            # สร้าง pattern สำหรับหารูป combo
            combo_patterns = [
                f"{main_type}+{second_type}",  # awning+fix
                f"{second_type}+{main_type}",  # fix+awning
                f"{main_type}_{second_type}",  # awning_fix
                f"{second_type}_{main_type}",  # fix_awning
            ]
            
            print(f"     Looking for patterns: {combo_patterns}")
            
            # หารูปที่ match กับ pattern
            image_extensions = ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG']
            all_image_files = []
            for ext in image_extensions:
                all_image_files.extend(self.base_photo_dir.glob(ext))
            
            for img_file in all_image_files:
                filename_lower = img_file.stem.lower()
                
                # ตรวจสอบว่า filename ตรงกับ pattern ไหน
                for pattern in combo_patterns:
                    if pattern in filename_lower:
                        print(f"  ✅ Found combo image: {img_file.name}")
                        return img_file
            
            print(f"  ⚠️ No combo image found for patterns: {combo_patterns}")
            print(f"  💡 Expected filename like: {combo_patterns[0]}.png")
            return None

        # ========== กรณีปกติ (ไม่มี +) ==========
        # วิเคราะห์ product type
        is_door = 'door' in product_type_lower or ref.startswith('D')
        is_window = 'window' in product_type_lower or ref.startswith('W')
        
        # ตรวจสอบ L/R
        is_left = bool(re.search(r'\bL\b', product_type, re.IGNORECASE))
        is_right = bool(re.search(r'\bR\b', product_type, re.IGNORECASE))
        
        # ตรวจสอบประเภทหลัก (ย้ายขึ้นมาก่อน เพื่อใช้ใน default logic)
        is_sliding = 'sliding' in product_type_lower
        is_awning = 'awning' in product_type_lower
        is_casement = 'casement' in product_type_lower
        is_fixed = 'fix' in product_type_lower
        is_airflow = 'airflow' in product_type_lower or 'air flow' in product_type_lower
        is_grill = 'grill' in product_type_lower
        
        # ตรวจสอบจำนวน panels (รองรับทั้ง (N), "N panels", และ "XPYT")
        num_panels = None
        
        panel_code_match = re.search(r'\b(\d+)[Pp]', product_type, re.IGNORECASE)
        if panel_code_match:
            num_panels = int(panel_code_match.group(1))
            print(f"  ℹ️  Detected {num_panels} panels from XP pattern (e.g., 3P3T)")
        
        # Pattern 2: (N) เช่น "Casement window (2)"
        elif re.search(r'\((\d+)\)', product_type_lower):
            panel_in_parenthesis = re.search(r'\((\d+)\)', product_type_lower)
            num_panels = int(panel_in_parenthesis.group(1))
            print(f"  ℹ️  Detected {num_panels} panels from (N) pattern")

        # Pattern 3: "N panels" หรือคำเฉพาะ
        elif '1 panel' in product_type_lower or 'single' in product_type_lower:
            num_panels = 1
        elif '2 panels' in product_type_lower or 'double' in product_type_lower:
            num_panels = 2
        elif '3 panels' in product_type_lower:
            num_panels = 3
        elif '4 panels' in product_type_lower:
            num_panels = 4
        elif '6 panels' in product_type_lower:
            num_panels = 6
        
        # ถ้าไม่ระบุจำนวนบาน → default = 1 บาน (ยกเว้น sliding ที่มักเป็น 2+)
        if num_panels is None:
            if is_sliding:
                num_panels = 2
                print(f"  ℹ️  Sliding without panel count, defaulting to 2 panels")
            elif is_casement and not is_left and not is_right:
                # ✅ Casement เดี่ยว (ไม่ระบุ (2)/double/2 panels) = single 1 บาน
                #    ถ้าเป็น double จริงจะมี "(2)" / "double" / "2 panels" ซึ่งถูกจับไปก่อนแล้ว
                num_panels = 1
                print(f"  ℹ️  Casement without panel count, defaulting to 1 panel (single)")
            else:
                num_panels = 1
                print(f"  ℹ️  No panel count specified, defaulting to 1 panel")
        
        # 🔥 DEBUG: แสดงข้อมูลที่วิเคราะห์ได้
        print(f"  📊 Analysis: door={is_door}, window={is_window}, panels={num_panels}, L={is_left}, R={is_right}")
        print(f"     sliding={is_sliding}, awning={is_awning}, casement={is_casement}, fixed={is_fixed}, airflow={is_airflow}, grill={is_grill}")
        
        best_match = None
        best_score = 0
        all_scores = []

        # รองรับหลายนามสกุลไฟล์
        image_extensions = ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG', '*.JPEG']
        all_image_files = []
        for ext in image_extensions:
            all_image_files.extend(self.base_photo_dir.glob(ext))
        
        # 🔥 DEBUG: แสดงไฟล์ทั้งหมดที่เจอ
        print(f"  🔍 Found {len(all_image_files)} image files in folder")
        if is_left or is_right:
            lr_files = [f.name for f in all_image_files if '-l.' in f.name.lower() or '-r.' in f.name.lower()]
            print(f"  🔍 L/R files found: {lr_files}")

        for img_file in all_image_files:
            filename_lower = img_file.stem.lower()
            
            # ข้ามไฟล์ที่มี '+' ในชื่อไฟล์ (สำหรับ combo type)
            if '+' in filename_lower:
                print(f"     ⏭️  Skip: {img_file.name} (has '+')")
                continue
            
            score = 0
            score_breakdown = []
            
            # 1. ตรวจสอบว่าเป็น door หรือ window
            if is_door and 'door' in filename_lower:
                score += 5
                score_breakdown.append("door:+5")
            elif is_window and 'window' in filename_lower:
                score += 5
                score_breakdown.append("window:+5")
            elif is_door and 'window' in filename_lower:
                score -= 10
                score_breakdown.append("wrong_type:-10")
            elif is_window and 'door' in filename_lower:
                score -= 10
                score_breakdown.append("wrong_type:-10")
            
            # 2. ตรวจสอบ L/R (สำคัญมาก!)
            # รองรับทั้ง -l/-L และ -r/-R
            has_l_suffix = bool(re.search(r'-l\.(png|jpg|jpeg)$', filename_lower, re.IGNORECASE))
            has_r_suffix = bool(re.search(r'-r\.(png|jpg|jpeg)$', filename_lower, re.IGNORECASE))
            
            if is_left and has_l_suffix:
                score += 30
                score_breakdown.append("L_match:+30")
            elif is_right and has_r_suffix:
                score += 30
                score_breakdown.append("R_match:+30")
            elif is_left and has_r_suffix:
                score -= 20
                score_breakdown.append("wrong_side:-20")
            elif is_right and has_l_suffix:
                score -= 20
                score_breakdown.append("wrong_side:-20")
            
            # 3. ตรวจสอบจำนวน panels
            if num_panels:
                matched_panel = False
                
                # Pattern สำหรับ single/double casement
                if num_panels == 1 and 'single-casement' in filename_lower:
                    score += 20
                    score_breakdown.append("single-casement:+20")
                    matched_panel = True
                elif num_panels == 2 and 'double-casement' in filename_lower:
                    score += 20
                    score_breakdown.append("double-casement:+20")
                    matched_panel = True
                
                # Pattern ต่างๆ ที่ควรตรวจสอบ (เพิ่มคะแนนให้สูงขึ้น)
                elif f'{num_panels}-panels' in filename_lower:
                    score += 40  # เพิ่มจาก 15 → 40
                    score_breakdown.append(f"{num_panels}-panels:+40")
                    matched_panel = True
                elif f'{num_panels}-fix' in filename_lower:
                    score += 40  # เพิ่มจาก 15 → 40
                    score_breakdown.append(f"{num_panels}-fix:+40")
                    matched_panel = True
                elif f'{num_panels}-awning' in filename_lower:
                    score += 40  # เพิ่มจาก 15 → 40
                    score_breakdown.append(f"{num_panels}-awning:+40")
                    matched_panel = True
                elif f'{num_panels}-airflow' in filename_lower:
                    score += 40  # เพิ่มจาก 15 → 40
                    score_breakdown.append(f"{num_panels}-airflow:+40")
                    matched_panel = True
                elif f'{num_panels}-slid' in filename_lower:
                    score += 40  # เพิ่มจาก 15 → 40
                    score_breakdown.append(f"{num_panels}-slid:+40")
                    matched_panel = True
                elif f'{num_panels}-casement' in filename_lower:
                    score += 40  # เพิ่มจาก 15 → 40
                    score_breakdown.append(f"{num_panels}-casement:+40")
                    matched_panel = True
                elif f'{num_panels}-' in filename_lower:
                    score += 35  # เพิ่มจาก 12 → 35
                    score_breakdown.append(f"{num_panels}-:+35")
                    matched_panel = True
                
                # ตรวจสอบเลขที่ขึ้นต้นและหักคะแนนหนักถ้าไม่ตรง
                file_numbers = re.findall(r'^(\d+)-', filename_lower)
                if file_numbers:
                    file_panel_num = int(file_numbers[0])
                    if file_panel_num == num_panels:
                        if not matched_panel:
                            score += 35  # เพิ่มจาก 10 → 35
                            score_breakdown.append(f"number_match:+35")
                    else:
                        # หักคะแนนหนักมาก แต่ถ้าเป็น single/double casement ให้อภัย
                        if not ('single-casement' in filename_lower or 'double-casement' in filename_lower):
                            score -= 50  # เพิ่มจาก -20 → -50
                            score_breakdown.append(f"wrong_panel:{file_panel_num}!={num_panels}:-50")
            
            # 4. ตรวจสอบประเภทหลัก
            if is_grill:
                if 'grill' in filename_lower:
                    score += 30
                    score_breakdown.append("grill:+30")
            
            if is_airflow:
                if 'airflow' in filename_lower or 'air-flow' in filename_lower:
                    score += 30
                    score_breakdown.append("airflow:+30")
                if 'fix' in filename_lower and 'airflow' not in filename_lower:
                    score -= 15
                    score_breakdown.append("fix_not_airflow:-15")
            
            if is_sliding:
                if 'slid' in filename_lower or 'panels-slid' in filename_lower:
                    score += 25
                    score_breakdown.append("sliding:+25")
                if 'fix' in filename_lower and 'slid' not in filename_lower:
                    score -= 15
                    score_breakdown.append("fix_not_sliding:-15")
                if 'casement' in filename_lower:
                    score -= 15
                    score_breakdown.append("casement_not_sliding:-15")
            
            if is_awning:
                if 'awning' in filename_lower:
                    score += 25
                    score_breakdown.append("awning:+25")
            
            if is_casement:
                if 'casement' in filename_lower:
                    score += 25
                    score_breakdown.append("casement:+25")
                if 'slid' in filename_lower:
                    score -= 15
                    score_breakdown.append("sliding_not_casement:-15")
            
            if is_fixed and not is_awning and not is_casement and not is_sliding and not is_airflow:
                if 'fix' in filename_lower and 'awning' not in filename_lower:
                    score += 25
                    score_breakdown.append("fixed:+25")
            
            # 5. ลดคะแนนถ้ามี awning แต่ product type ไม่ใช่ awning
            if not is_awning and 'awning' in filename_lower:
                score -= 15
                score_breakdown.append("unwanted_awning:-15")
            
            # 6. ลดคะแนนถ้ามี airflow แต่ product type ไม่ใช่ airflow
            if not is_airflow and 'airflow' in filename_lower:
                score -= 15
                score_breakdown.append("unwanted_airflow:-15")
            
            all_scores.append((img_file.name, score, score_breakdown))
            
            if score > best_score:
                best_score = score
                best_match = img_file
                print(f"     ⭐ New best: {img_file.name} (score: {score}) {score_breakdown}")

        # DEBUG: แสดงคะแนนทั้งหมด
        print(f"\n  📊 Top 5 scores:")
        for filename, score, breakdown in sorted(all_scores, key=lambda x: x[1], reverse=True)[:5]:
            print(f"     {filename}: {score} - {breakdown}")
        print()

        if best_match and best_score > 0:
            print(f"  ✅ Found: {best_match.name} (score: {best_score})")
            return best_match

        # fallback
        print("  ⚠️ No match found, using fallback image.")
        if all_image_files:
            # ข้ามไฟล์ที่มี '+' ใน fallback ด้วย
            filtered_images = [img for img in all_image_files if '+' not in img.stem.lower()]
            if filtered_images:
                # เรียงตามจำนวนบานก่อน (เลือกที่มีบานน้อยสุด)
                filtered_images.sort(key=lambda x: int(re.findall(r'^(\d+)-', x.stem.lower())[0]) if re.findall(r'^(\d+)-', x.stem.lower()) else 999)
                
                for img in filtered_images:
                    if 'fix' in img.stem.lower():
                        return img
                return filtered_images[0]

        print(f"  ❌ No image found in {self.base_photo_dir}")
        return None
    
    def generate_panel_image_with_ai(self, base_image_path: Path, 
                                    width: int, height: int, 
                                    product_type: str, ref: str) -> Optional[Path]:
        """
        Generate panel image with dimensions annotated
        """
        try:
            if not base_image_path or not base_image_path.exists():
                print(f"  ❌ Base image not found: {base_image_path}")
                return None
            
            print(f"  🎨 Generating annotated image...")
            print(f"     Base: {base_image_path.name}")
            print(f"     Dimensions: {width}x{height} mm")  # ✅ ใช้ค่าที่ส่งมาจาก product
            
            # Create annotated image with dimensions
            output_path = self._create_annotated_image(
                base_image_path=base_image_path,
                width=width,
                height=height,  # ✅ ค่านี้มาจาก calculated_height แล้ว
                ref=ref,
                description=product_type
            )
            
            if output_path and output_path.exists():
                print(f"  ✅ Generated: {output_path.name}")
                return output_path
            else:
                print(f"  ❌ Failed to generate image")
                return None
                
        except Exception as e:
            print(f"  ❌ Error generating panel image: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _create_annotated_image(self, base_image_path: Path, 
                                width: int, height: int, 
                                ref: str, description: str) -> Path:
        """สร้างรูปพร้อมใส่ annotations แบบมีลูกศร รองรับหลาย panels"""
        try:
            # โหลดรูปต้นแบบ
            img = Image.open(base_image_path)
            
            # Resize โดยจำกัดไม่เกิน max_width x max_height
            max_width = 300
            max_height = 300

            orig_w, orig_h = img.size
            scale_w = max_width / orig_w
            scale_h = max_height / orig_h
            scale = min(scale_w, scale_h, 1.0)

            target_width = int(orig_w * scale)
            target_height = int(orig_h * scale)

            img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
            
            # ตรวจหาจำนวน panels จาก description
            num_panels = 1
            panel_match = re.search(r'\((\d+)\)', description)
            if panel_match:
                num_panels = int(panel_match.group(1))
            
            # คำนวณความกว้างแต่ละ panel
            individual_width = width // num_panels if num_panels > 1 else width
            
            # สร้าง canvas ใหม่พร้อมพื้นที่สำหรับข้อมูล
            # เพิ่ม margin_top ถ้ามีหลาย panels (ต้องการพื้นที่สำหรับ total width)
            margin_top = 120 if num_panels > 1 else 80
            margin_left = 80
            margin_right = 20
            margin_bottom = 40
            
            canvas_width = margin_left + target_width + margin_right
            canvas_height = margin_top + target_height + margin_bottom
            
            canvas = Image.new('RGB', (canvas_width, canvas_height), 'white')
            canvas.paste(img, (margin_left, margin_top))
            
            # วาดข้อมูล dimensions
            draw = ImageDraw.Draw(canvas)
            
            try:
                font = ImageFont.truetype("arial.ttf", 10)
                font_bold = ImageFont.truetype("arialbd.ttf", 11)
            except:
                font = ImageFont.load_default()
                font_bold = ImageFont.load_default()
            
            # ========== Width Annotations ==========
            if num_panels > 1:
                # === Total Width (ด้านบนสุด) ===
                total_arrow_y = margin_top - 60
                start_x = margin_left
                end_x = margin_left + target_width
                
                # เส้นแนวนอนสำหรับ total width
                draw.line([(start_x, total_arrow_y), (end_x, total_arrow_y)], 
                        fill='black', width=1)
                
                # ลูกศรซ้าย-ขวา
                self._draw_arrow_head(draw, start_x, total_arrow_y, 
                                    direction='left', size=5)
                self._draw_arrow_head(draw, end_x, total_arrow_y, 
                                    direction='right', size=5)
                
                # ข้อความ Total Width
                text_total = f"W = {width}"
                bbox = draw.textbbox((0, 0), text_total, font=font_bold)
                text_width = bbox[2] - bbox[0]
                # ✅ จัดกึ่งกลางตามลูกศร (ช่วง start_x..end_x) ไม่ใช่กึ่งกลาง canvas
                text_x = start_x + (target_width - text_width) // 2
                text_y = total_arrow_y - 20
                draw.text((text_x, text_y), text_total, fill='black', font=font_bold)
                
                # === Individual Panel Widths (ด้านล่าง total width) ===
                panel_arrow_y = margin_top - 30
                panel_width_px = target_width / num_panels
                
                for i in range(num_panels):
                    panel_start_x = margin_left + int(i * panel_width_px)
                    panel_end_x = margin_left + int((i + 1) * panel_width_px)
                    
                    # เส้นแนวนอนสำหรับแต่ละ panel
                    draw.line([(panel_start_x, panel_arrow_y), 
                            (panel_end_x, panel_arrow_y)], 
                            fill='black', width=1)
                    
                    # ลูกศรซ้าย-ขวา
                    self._draw_arrow_head(draw, panel_start_x, panel_arrow_y, 
                                        direction='left', size=5)
                    self._draw_arrow_head(draw, panel_end_x, panel_arrow_y, 
                                        direction='right', size=5)
                    
                    # ข้อความความกว้างแต่ละ panel
                    text_panel = f"W = {individual_width}"
                    bbox = draw.textbbox((0, 0), text_panel, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_x = panel_start_x + (panel_width_px - text_width) / 2
                    text_y = panel_arrow_y - 18
                    draw.text((int(text_x), int(text_y)), text_panel, 
                            fill='black', font=font)
            
            else:
                # === Single Panel Width (แบบเดิม) ===
                arrow_y = margin_top - 30
                start_x = margin_left
                end_x = margin_left + target_width
                
                # เส้นแนวนอน
                draw.line([(start_x, arrow_y), (end_x, arrow_y)], 
                        fill='black', width=1)
                
                # ลูกศรซ้าย-ขวา
                self._draw_arrow_head(draw, start_x, arrow_y, 
                                    direction='left', size=5)
                self._draw_arrow_head(draw, end_x, arrow_y, 
                                    direction='right', size=5)
                
                # ข้อความ W
                text_w = f"W = {width}"
                bbox = draw.textbbox((0, 0), text_w, font=font_bold)
                text_width = bbox[2] - bbox[0]
                # ✅ จัดกึ่งกลางตามลูกศร (ช่วง start_x..end_x) ไม่ใช่กึ่งกลาง canvas
                text_x = start_x + (target_width - text_width) // 2
                text_y = arrow_y - 20
                draw.text((text_x, text_y), text_w, fill='black', font=font_bold)
            
            # ========== Height (H) - ด้านซ้าย (เหมือนเดิม) ==========
            arrow_x = margin_left - 30
            start_y = margin_top
            end_y = margin_top + target_height
            
            # เส้นแนวตั้ง
            draw.line([(arrow_x, start_y), (arrow_x, end_y)], 
                    fill='black', width=1)
            
            # ลูกศรบน-ล่าง
            self._draw_arrow_head(draw, arrow_x, start_y, direction='up', size=5)
            self._draw_arrow_head(draw, arrow_x, end_y, direction='down', size=5)
            
            # ข้อความ H (หมุนตามแนวตั้ง)
            text_h = f"H = {height}"
            # ✅ ทำ temp image ให้พอดีกับข้อความ เพื่อให้จัดกึ่งกลางได้แม่นยำหลังหมุน
            hb = draw.textbbox((0, 0), text_h, font=font_bold)
            th_w = hb[2] - hb[0]
            th_h = hb[3] - hb[1]
            temp_img = Image.new('RGBA', (th_w + 4, th_h + 4), (255, 255, 255, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            temp_draw.text((2 - hb[0], 2 - hb[1]), text_h, fill='black', font=font_bold)
            rotated = temp_img.rotate(90, expand=True)
            # ✅ จัดกึ่งกลางตามลูกศรแนวตั้ง (ช่วง start_y..end_y) ไม่ใช่กึ่งกลาง canvas
            text_x_pos = arrow_x - 8 - rotated.width
            text_y_pos = start_y + (target_height - rotated.height) // 2
            canvas.paste(rotated, (text_x_pos, text_y_pos), rotated)
            
            # บันทึกรูป
            safe_ref = ref.replace('/', '_')
            output_filename = f"{safe_ref}_{width}x{height}.png"
            output_path = self.output_dir / output_filename
            
            # ✅ สร้างโฟลเดอร์ถ้ายังไม่มี
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            canvas.save(output_path)
            
            print(f"  ✅ Created annotated image: {output_path}")
            if num_panels > 1:
                print(f"     Panels: {num_panels} x {individual_width}mm = {width}mm total")
            
            return output_path
            
        except Exception as e:
            print(f"  ❌ Error creating annotated image: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ==================================================
    # รูปประกอบหลายบาน (composed / stacked drawings)
    # ==================================================

    def _resolve_segment_images(self, segments: List[Dict], ref: str,
                                lr_side: str = None) -> Optional[List[Path]]:
        """
        หารูปต้นแบบของแต่ละบานย่อย (ใช้ตัวหาแบบ single type เดิม)

        lr_side: ถ้าระบุ 'L' จะแทน "L/R" ใน type ด้วย 'L'
        """
        paths = []

        for seg in segments:
            seg_type = (seg.get('product_type') or '').strip()
            seg_type = seg_type.rstrip('+').strip()

            if lr_side:
                seg_type = re.sub(r'\bL\s*/\s*R\b', lr_side, seg_type, flags=re.IGNORECASE)

            if not seg_type:
                print(f"  ❌ Segment {seg.get('ref')} has no product type")
                return None

            path = self.match_product_type_to_image(seg_type, seg.get('ref', ref))

            if not path:
                print(f"  ❌ No base image for segment {seg.get('ref')} ('{seg_type}')")
                return None

            paths.append(path)

        return paths

    def _compose_panel(self, image_paths: List[Path], segments: List[Dict],
                       direction: str, total_width: int,
                       total_height: int) -> Tuple[Image.Image, List[Tuple]]:
        """
        ต่อรูปบานย่อยเป็นบานเดียว โดยให้สัดส่วนของแต่ละบานตรงตามขนาดจริง

        Returns:
            (panel_image, boxes) โดย boxes = [(start_px, end_px, ขนาด mm), ...]
        """
        # ขนาด canvas ตามสัดส่วนจริงของสินค้า (variant A)
        aspect = total_width / total_height

        if aspect >= 1:
            panel_w = COMPOSED_LONG_SIDE_PX
            panel_h = max(COMPOSED_MIN_SIDE_PX, int(round(COMPOSED_LONG_SIDE_PX / aspect)))
        else:
            panel_h = COMPOSED_LONG_SIDE_PX
            panel_w = max(COMPOSED_MIN_SIDE_PX, int(round(COMPOSED_LONG_SIDE_PX * aspect)))

        panel = Image.new('RGB', (panel_w, panel_h), 'white')
        boxes = []
        cursor = 0

        for i, (img_path, seg) in enumerate(zip(image_paths, segments)):
            is_last = (i == len(segments) - 1)
            img = Image.open(img_path).convert('RGB')

            if direction == 'vertical':
                seg_mm = int(seg.get('height') or 0)
                size_px = (panel_h - cursor) if is_last else int(round(panel_h * seg_mm / total_height))
                size_px = max(1, size_px)
                img = img.resize((panel_w, size_px), Image.Resampling.LANCZOS)
                panel.paste(img, (0, cursor))
            else:
                seg_mm = int(seg.get('width') or 0)
                size_px = (panel_w - cursor) if is_last else int(round(panel_w * seg_mm / total_width))
                size_px = max(1, size_px)
                img = img.resize((size_px, panel_h), Image.Resampling.LANCZOS)
                panel.paste(img, (cursor, 0))

            boxes.append((cursor, cursor + size_px, seg_mm, seg))
            cursor += size_px

        return panel, boxes

    def _draw_rotated_text(self, canvas: Image.Image, draw: ImageDraw,
                           text: str, font, center_x: int,
                           y_start: int, y_end: int):
        """เขียนข้อความแนวตั้ง จัดกึ่งกลางช่วง y_start..y_end"""
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

        tmp = Image.new('RGBA', (tw + 4, th + 4), (255, 255, 255, 0))
        ImageDraw.Draw(tmp).text((2 - bbox[0], 2 - bbox[1]), text, fill='black', font=font)
        rotated = tmp.rotate(90, expand=True)

        canvas.paste(
            rotated,
            (int(center_x - rotated.width / 2),
             int(y_start + (y_end - y_start - rotated.height) / 2)),
            rotated
        )

    def _annotate_composed(self, panel: Image.Image, boxes: List[Tuple],
                           direction: str, total_width: int,
                           total_height: int) -> Image.Image:
        """
        ใส่ dimension ให้รูปประกอบ

        ด้านบน (หยาบ -> ละเอียด จากบนลงล่าง):
            แถว 1  ความกว้างรวมทั้งชุด
            แถว 2  ความกว้างของแต่ละบาน (เฉพาะตอนต่อแนวนอน)
            แถว 3+ ความกว้างของช่องย่อยในบานที่มีหลายช่อง (เช่น บานเลื่อน 2 ช่อง)
        ด้านซ้าย  ความสูงรวมทั้งชุด
        ด้านขวา   ความสูงของแต่ละบาน (เฉพาะตอนต่อแนวตั้ง)
        """
        # scale ทุกอย่างตามความละเอียดของรูป (baseline = 300px)
        scale = max(1.0, max(panel.width, panel.height) / 300.0)
        px = lambda v: max(1, int(round(v * scale)))

        font_bold = _load_font(px(11), bold=True)
        font_regular = _load_font(px(10))

        # ---------- วางแผนแถว dimension ด้านบน ----------
        # ⚠️ ไม่แสดงความกว้างของช่องย่อยภายในบานเดียว
        #    เช่น "2 panels 2 tracks sliding door" คือชุดเดียวที่มี 2 บาน
        #    ความกว้าง 1705 คือทั้งชุด ไม่ต้องหารครึ่งเป็น 852 + 852
        #    แสดงเฉพาะขนาดที่มาจากรายการจริงใน quotation เท่านั้น
        top_rows = [('total', None)]

        if direction == 'horizontal' and len(boxes) > 1:
            top_rows.append(('segments', None))

        row_gap = px(30)
        m_left = px(60)
        m_right = px(70) if direction == 'vertical' else px(24)
        m_top = px(46) + (len(top_rows) - 1) * row_gap
        m_bottom = px(16)

        canvas = Image.new(
            'RGB',
            (m_left + panel.width + m_right, m_top + panel.height + m_bottom),
            'white'
        )
        canvas.paste(panel, (m_left, m_top))
        draw = ImageDraw.Draw(canvas)

        head = px(5)
        x0, x1 = m_left, m_left + panel.width
        y0, y1 = m_top, m_top + panel.height

        def row_y(index: int) -> int:
            """แถวสุดท้ายอยู่ชิดรูปที่สุด แถวแรกอยู่บนสุด"""
            return m_top - px(22) - (len(top_rows) - 1 - index) * row_gap

        def h_dimension(start_x, end_x, y, label, font, big_head=False):
            if end_x <= start_x:
                return
            draw.line([(start_x, y), (end_x, y)], fill='black', width=px(1))
            size = head if big_head else px(4)
            self._draw_arrow_head(draw, start_x, y, 'left', size)
            self._draw_arrow_head(draw, end_x, y, 'right', size)

            bbox = draw.textbbox((0, 0), label, font=font)
            draw.text(
                (start_x + ((end_x - start_x) - (bbox[2] - bbox[0])) // 2, y - px(17)),
                label, fill='black', font=font
            )

        # ---------- แถว dimension ด้านบน ----------
        for index, (kind, payload) in enumerate(top_rows):
            y = row_y(index)

            if kind == 'total':
                h_dimension(x0, x1, y, f"W = {total_width}", font_bold, big_head=True)

            elif kind == 'segments':
                # ความกว้างของแต่ละบาน (แต่ละรายการใน quotation) ตอนต่อแนวนอน
                for (start_px, end_px, seg_mm, _seg) in boxes:
                    h_dimension(m_left + start_px + 1, m_left + end_px - 1, y,
                                str(seg_mm), font_regular)

        # ---------- ความสูงรวม (ด้านซ้าย) ----------
        height_arrow_x = m_left - px(30)
        draw.line([(height_arrow_x, y0), (height_arrow_x, y1)], fill='black', width=px(1))
        self._draw_arrow_head(draw, height_arrow_x, y0, 'up', head)
        self._draw_arrow_head(draw, height_arrow_x, y1, 'down', head)
        self._draw_rotated_text(canvas, draw, f"H = {total_height}", font_bold,
                                height_arrow_x - px(12), y0, y1)

        # ---------- ความสูงของแต่ละบาน (ด้านขวา, เฉพาะต่อแนวตั้ง) ----------
        if direction == 'vertical':
            seg_arrow_x = m_left + panel.width + px(22)

            for (start_px, end_px, seg_mm, _seg) in boxes:
                sy0, sy1 = m_top + start_px, m_top + end_px
                draw.line([(seg_arrow_x, sy0 + 1), (seg_arrow_x, sy1 - 1)],
                          fill='black', width=px(1))
                self._draw_arrow_head(draw, seg_arrow_x, sy0 + 1, 'up', px(4))
                self._draw_arrow_head(draw, seg_arrow_x, sy1 - 1, 'down', px(4))
                self._draw_rotated_text(canvas, draw, str(seg_mm), font_regular,
                                        seg_arrow_x + px(12), sy0, sy1)

        return canvas

    def _process_composed_product(self, ref: str, product_type: str,
                                  segments: List[Dict]) -> Optional[Path]:
        """
        สร้างรูปบานที่ประกอบจากหลาย product ใน quotation
        (เช่น Awning อยู่บน + Fixed อยู่ล่าง)
        """
        try:
            from PIL import ImageOps

            direction, total_width, total_height = determine_layout(segments, ref)

            print(f"  🧩 Composing {len(segments)} segments for {ref} "
                  f"({direction}, total {total_width}x{total_height} mm)")
            for seg in segments:
                print(f"     - {seg.get('ref')}: {seg.get('width')}x{seg.get('height')} "
                      f"'{seg.get('product_type')}'")

            # บาน L/R ต้องสร้าง 2 ชุด (R ได้จากการ mirror ของ L)
            has_lr = any(
                re.search(r'\bL\s*/\s*R\b', seg.get('product_type') or '', re.IGNORECASE)
                for seg in segments
            )

            image_paths = self._resolve_segment_images(
                segments, ref, lr_side='L' if has_lr else None
            )

            if not image_paths:
                return None

            panel, boxes = self._compose_panel(
                image_paths, segments, direction, total_width, total_height
            )

            safe_ref = ref.replace('/', '_')

            if not has_lr:
                canvas = self._annotate_composed(
                    panel, boxes, direction, total_width, total_height
                )
                output_path = self.output_dir / f"{safe_ref}_{total_width}x{total_height}.png"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                canvas.save(output_path)

                print(f"  ✅ Composed image saved: {output_path.name}")
                return output_path

            # ---- L/R: mirror บานที่ประกอบแล้วเพื่อสร้างฝั่ง R ----
            print(f"  🔁 Segment has L/R - creating mirrored R composition")

            canvas_l = self._annotate_composed(
                panel, boxes, direction, total_width, total_height
            )
            canvas_r = self._annotate_composed(
                ImageOps.mirror(panel), boxes, direction, total_width, total_height
            )

            path_l = self.output_dir / f"{safe_ref}_L_{total_width}x{total_height}.png"
            path_r = self.output_dir / f"{safe_ref}_R_{total_width}x{total_height}.png"
            path_l.parent.mkdir(parents=True, exist_ok=True)
            canvas_l.save(path_l)
            canvas_r.save(path_r)

            return self._combine_lr_images(path_l, path_r, ref, total_width, total_height)

        except Exception as e:
            print(f"  ❌ Error composing image for {ref}: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _draw_arrow_head(self, draw: ImageDraw, x: int, y: int,
                        direction: str, size: int = 5):
        """
        วาดหัวลูกศร
        
        Args:
            draw: ImageDraw object
            x, y: ตำแหน่งจุดยอดลูกศร
            direction: ทิศทาง 'left', 'right', 'up', 'down'
            size: ขนาดลูกศร
        """
        if direction == 'left':
            # ลูกศรชี้ซ้าย: 
            points = [
                (x, y),
                (x + size, y - size),
                (x + size, y + size)
            ]
        elif direction == 'right':
            # ลูกศรชี้ขวา: >
            points = [
                (x, y),
                (x - size, y - size),
                (x - size, y + size)
            ]
        elif direction == 'up':
            # ลูกศรชี้บน: ^
            points = [
                (x, y),
                (x - size, y + size),
                (x + size, y + size)
            ]
        elif direction == 'down':
            # ลูกศรชี้ล่าง: v
            points = [
                (x, y),
                (x - size, y - size),
                (x + size, y - size)
            ]
        else:
            return
        
        draw.polygon(points, fill='black')
    
    def _create_simple_diagram(self, base_image_path: Path, 
                               width: int, height: int, ref: str) -> Path:
        """สร้างรูปแบบง่าย (fallback)"""
        return self._create_annotated_image(base_image_path, width, height, ref, "")
    
    def process_product(self, product: Dict) -> Optional[Path]:
        """
        ประมวลผลสินค้า 1 รายการ
        ถ้ามี L/R จะสร้างรูป 2 แบบ (L และ R)
        """
        ref = product.get('ref', '')
        product_type = product.get('product_type', '')
        width = product.get('width', 0)
        
       # ✅ ใช้ calculated_height ถ้ามี (คำนวณไว้แล้วจาก pre_calculate_heights)
        height = product.get('calculated_height') or product.get('height', 0)
        
        # Debug
        if product.get('calculated_height'):
            print(f"📏 Using calculated_height: {height} for {ref}")
        else:
            print(f"📏 Using original height: {height} for {ref}")
        
        if not all([ref, product_type, width, height]):
            print(f"⚠️ Missing data for product: {ref}")
            return None
        
        print(f"\n{'='*60}")
        print(f"Processing: {ref} - {product_type}")
        print(f"Dimensions: {width}x{height} mm")
    
        # ✅ เพิ่มส่วนนี้ก่อนเช็คข้อมูล
        # ตรวจสอบและคูณ width ก่อนสร้างรูป
        if width > 0 and product_type:
            panel_match = re.search(r'\((\d+)\)', product_type)
            if panel_match:
                num_panels = int(panel_match.group(1))
                if num_panels > 1:
                    original_width = width
                    width = original_width * num_panels
                    product['width'] = width  # อัพเดทใน product dict
                    print(f"🔢 Multiplied width for {ref}: {original_width} × {num_panels} = {width}")
        
        if not all([ref, product_type, width, height]):
            print(f"⚠️ Missing data for product: {ref}")
            return None
        
        print(f"\n{'='*60}")
        print(f"Processing: {ref} - {product_type}")
        print(f"Dimensions: {width}x{height} mm")
        
        # 🔥 บานประกอบหลายตัว (เช่น Awning บน + Fixed ล่าง)
        #    วาดจากรูป single type ต่อกันตามสัดส่วนจริง
        #    มาก่อนรูป combo สำเร็จรูป เพราะได้สัดส่วนตรงตามขนาดใน quotation
        segments = get_panel_segments(product)

        if len(segments) >= 2:
            composed = self._process_composed_product(ref, product_type, segments)

            if composed:
                print(f"{'='*60}\n")
                return composed

            print(f"  ⚠️ Composition failed for {ref}, falling back to single/combo image")

        # 🔥 ตรวจสอบว่ามี L/R หรือไม่
        has_lr = bool(re.search(r'\bL\s*/\s*R\b', product_type, re.IGNORECASE))

        if has_lr:
            print(f"  🔄 Detected L/R pattern - will generate both L and R images")
            return self._process_lr_product(ref, product_type, width, height)
        else:
            # 1. หารูปต้นแบบ
            base_image = self.match_product_type_to_image(product_type, ref)
            
            if not base_image:
                print(f"❌ No base image found for {ref}")
                return None
            
            # 2. สร้างรูปพร้อม dimensions (ใช้ width ที่คูณแล้ว)
            generated_image = self.generate_panel_image_with_ai(
                base_image, width, height, product_type, ref
            )
            
            print(f"{'='*60}\n")
            return generated_image
    
    def _process_lr_product(self, ref: str, product_type: str, 
                            width: int, height: int) -> Optional[Path]:
        """
        ประมวลผล product ที่มี L/R โดย:
        ✅ หาเฉพาะรูป L
        ✅ สร้างรูป R โดย mirror จาก L (ไม่ใช้ไฟล์ R จริง)
        ✅ สร้าง annotated image ทั้ง L และ R
        ✅ รวมเป็นภาพเดียวแนวนอน
        """
        try:
            from PIL import Image, ImageOps

            # 1) Normalize type (เช่น swing -> casement)
            normalized_type = product_type.replace('swing', 'casement')

            # 2) หา L เท่านั้น
            base_image_l = None
            is_door = 'door' in normalized_type.lower() or ref.startswith('D')
            is_window = 'window' in normalized_type.lower() or ref.startswith('W')

            # หา L image ตาม type ที่ถูกต้อง
            base_image_l = self.match_product_type_to_image(
                normalized_type.replace('L/R', 'L'),
                ref
            )

            # ถ้าไม่เจอ L → fallback ใช้ type เดิม
            if not base_image_l:
                print(f"  ⚠️ No L image found for {'door' if is_door else 'window'}, cannot fallback to other type")
                return None

            print(f"  ✅ Found L image: {base_image_l.name}")

            # 3) Flip L → temp R
            img_l = Image.open(base_image_l)
            img_r_flipped = ImageOps.mirror(img_l)

            temp_r_path = self.output_dir / f"{ref}_temp_R.png"
            img_r_flipped.save(temp_r_path)
            base_image_r = temp_r_path

            print(f"  🔁 Created R image by flipping L")

            # 4) สร้าง annotated image (ใช้ชื่อไฟล์แยกชัดเจน)
            image_l = self._create_annotated_image(
                base_image_path=base_image_l,
                width=width,
                height=height,
                ref=f"{ref}_L",
                description=f"{product_type} (L)"
            )

            image_r = self._create_annotated_image(
                base_image_path=base_image_r,
                width=width,
                height=height,
                ref=f"{ref}_R",
                description=f"{product_type} (R)"
            )

            # 5) รวม L และ R เป็นภาพเดียว
            combined_image = self._combine_lr_images(image_l, image_r, ref, width, height)

            print(f"  ✅ Created combined L/R image: {combined_image}")
            return combined_image

        except Exception as e:
            print(f"  ❌ Error processing L/R product: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _combine_lr_images(self, image_l_path: Path, image_r_path: Path,
                          ref: str, width: int, height: int) -> Path:
        """
        รวมรูป L และ R เป็นรูปเดียวแนวนอน พร้อมเขียน "L" และ "R" ด้านบน
        """
        try:
            img_l = Image.open(image_l_path)
            img_r = Image.open(image_r_path)
            
            # สร้าง canvas ใหม่ (แนวนอน)
            # ✅ scale ตามความละเอียดของรูป (รูปประกอบมีขนาดใหญ่กว่ารูปเดี่ยวมาก)
            label_scale = max(1.0, max(img_l.width, img_l.height) / 300.0)
            spacing = int(20 * label_scale)      # ระยะห่างระหว่างรูป
            label_height = int(30 * label_scale)  # พื้นที่สำหรับเขียน "L" และ "R"

            total_width = img_l.width + spacing + img_r.width
            total_height = max(img_l.height, img_r.height) + label_height
            
            combined = Image.new('RGB', (total_width, total_height), 'white')
            
            # วาง L ด้านซ้าย
            combined.paste(img_l, (0, label_height))
            
            # วาง R ด้านขวา
            combined.paste(img_r, (img_l.width + spacing, label_height))
            
            # เขียน "L" และ "R" ด้านบน
            draw = ImageDraw.Draw(combined)
            
            font = _load_font(int(20 * label_scale), bold=True)
            label_y = int(5 * label_scale)

            # เขียน "L" กลางรูปซ้าย
            bbox_l = draw.textbbox((0, 0), "L", font=font)
            text_width_l = bbox_l[2] - bbox_l[0]
            text_x_l = (img_l.width - text_width_l) // 2
            draw.text((text_x_l, label_y), "L", fill='red', font=font)

            # เขียน "R" กลางรูปขวา
            bbox_r = draw.textbbox((0, 0), "R", font=font)
            text_width_r = bbox_r[2] - bbox_r[0]
            text_x_r = img_l.width + spacing + (img_r.width - text_width_r) // 2
            draw.text((text_x_r, label_y), "R", fill='blue', font=font)
            
            # บันทึก
            output_filename = f"{ref}_{width}x{height}_LR.png"
            output_path = self.output_dir / output_filename
            combined.save(output_path)
            
            print(f"  ✅ Combined L/R image saved: {output_path}")
            
            # ลบรูปชั่วคราว L และ R
            try:
                image_l_path.unlink()
                image_r_path.unlink()
            except:
                pass
            
            return output_path
            
        except Exception as e:
            print(f"  ❌ Error combining L/R images: {e}")
            import traceback
            traceback.print_exc()
            return None

def generate_images_for_site_survey(products: List[Dict], 
                                    base_photo_dir: str = "TOSTEM Drawing",
                                    openai_api_key: str = None,
                                    openai_base_url: str = None) -> Dict[str, Path]:
    """
    สร้างรูปภาพสำหรับทุก product ใน site survey
    
    Args:
        products: รายการสินค้าทั้งหมด
        base_photo_dir: โฟล์เดอร์รูปต้นแบบ (default: TOSTEM Drawing)
        openai_api_key: OpenAI API key
        openai_base_url: OpenAI base URL
        
    Returns:
        Dictionary {ref: image_path}
    """
    generator = WindowDoorImageGenerator(
        base_photo_dir=base_photo_dir,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url
    )
    
    results = {}
    
    print(f"\n🚀 Generating images for {len(products)} products...")
    print("="*80)
    
    for product in products:
        ref = product.get('ref', '')
        
        if not ref:
            continue
        
        image_path = generator.process_product(product)
        
        
        if image_path:
            results[ref] = image_path
            print(f"✅ {ref}: {image_path}")
        else:
            print(f"❌ {ref}: Failed")
    
    print("="*80)
    print(f"✅ Generated {len(results)}/{len(products)} images")
    
    return results