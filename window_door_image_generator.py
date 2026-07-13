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
            spacing = 20  # ระยะห่างระหว่างรูป
            label_height = 30  # พื้นที่สำหรับเขียน "L" และ "R"
            
            total_width = img_l.width + spacing + img_r.width
            total_height = max(img_l.height, img_r.height) + label_height
            
            combined = Image.new('RGB', (total_width, total_height), 'white')
            
            # วาง L ด้านซ้าย
            combined.paste(img_l, (0, label_height))
            
            # วาง R ด้านขวา
            combined.paste(img_r, (img_l.width + spacing, label_height))
            
            # เขียน "L" และ "R" ด้านบน
            draw = ImageDraw.Draw(combined)
            
            try:
                font = ImageFont.truetype("arialbd.ttf", 20)
            except:
                font = ImageFont.load_default()
            
            # เขียน "L" กลางรูปซ้าย
            bbox_l = draw.textbbox((0, 0), "L", font=font)
            text_width_l = bbox_l[2] - bbox_l[0]
            text_x_l = (img_l.width - text_width_l) // 2
            draw.text((text_x_l, 5), "L", fill='red', font=font)
            
            # เขียน "R" กลางรูปขวา
            bbox_r = draw.textbbox((0, 0), "R", font=font)
            text_width_r = bbox_r[2] - bbox_r[0]
            text_x_r = img_l.width + spacing + (img_r.width - text_width_r) // 2
            draw.text((text_x_r, 5), "R", fill='blue', font=font)
            
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