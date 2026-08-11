#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# use of site_survey_generator.py

"""
Quotation Processor Module
ระบบประมวลผลไฟล์ Quotation พร้อม Smart Mosquito Detection
รองรับไฟล์: PDF, Excel (.xlsx, .xls), CSV
"""

import os
import re
import pandas as pd
from typing import Dict, List, Any, Optional
from datetime import datetime

# PDF processing
try:
    import pdfplumber
    PDF_SUPPORT = True
except ImportError:
    print("Warning: pdfplumber not available. Install with: pip install pdfplumber")
    PDF_SUPPORT = False


class EnhancedTOSTEMQuotationProcessor:
    """Enhanced TOSTEM PDF processor with multi-line description support"""
    
    def __init__(self):
        self.debug_info = []
    
    def process_tostem_quotation_pdf(self, file_path: str) -> Dict[str, Any]:
        """Main entry point for processing TOSTEM PDF quotation with enhanced multi-line support"""
        return self._process_pdf_directly(file_path)

    def _process_pdf_directly(self, file_path: str) -> Dict[str, Any]:
        """Process PDF file directly with enhanced multi-line handling"""
        try:
            import pdfplumber
            
            full_text = ""
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    full_text += page_text + "\n"
            
            if not full_text.strip():
                raise ValueError("ไม่สามารถแยกข้อความจากไฟล์ PDF ได้")
            
            # Enhanced text processing for multi-line descriptions
            project_info = self._extract_project_info_from_text(full_text)
            products = self._extract_products_with_multiline_support(full_text)
            
            # ✅ เพิ่มสีจาก project_info ให้กับทุก product
            default_color = project_info.get('color', '')
            print(f"\n🎨 Applying color '{default_color}' to all products...")

            for product in products:
                if not product.get('color'):  # ถ้า product ยังไม่มีสี
                    product['color'] = default_color
                    print(f"   ✓ Applied color to {product.get('ref', 'Unknown')}: '{default_color}'")

            # ✅ ถ้าอ่านไฟล์ได้แต่ไม่เจอ product เลย = แปลว่ารหัส Code ในใบเสนอราคา
            #    ไม่ตรงกับ pattern ที่รองรับ ต้องแจ้งเตือน ไม่ใช่เงียบแล้วสร้างเอกสารเปล่า
            result = {
                'success': True,
                'data': {
                    'project_info': project_info,
                    'products': products,
                    'summary': self._calculate_summary(products)
                },
                'message': f'ประมวลผล PDF สำเร็จ ({len(products)} รายการ) - Enhanced multi-line support'
            }

            if not products:
                warning = ('ไม่พบรายการสินค้าในใบเสนอราคา - '
                           'รหัส Code อาจไม่อยู่ในรูปแบบที่รองรับ (เช่น W1, D1, AD1, ADD1) '
                           'กรุณาตรวจสอบคอลัมน์ Code')
                result['warning'] = warning
                result['message'] = f'⚠️ {warning}'
                print(f"\n⚠️ {warning}")

            return result

        except Exception as e:
            return {
                'success': False,
                'data': {},
                'message': f'เกิดข้อผิดพลาด: {str(e)}'
            }

    def _extract_products_with_multiline_support(self, text: str) -> List[Dict[str, Any]]:
        """Extract products with enhanced multi-line description support"""
        products = []
        
        try:
            lines = text.split('\n')
            
            # Step 1: ระบุบรรทัดที่เป็น product lines หลัก (รวม F และ T)
            product_line_indices = []
            for i, line in enumerate(lines):
                line = line.strip()
                if self._is_main_product_line(line):
                    product_line_indices.append(i)
                    # Debug: แสดงว่าเจออะไร
                    ref_match = re.match(r'^([DW][A-Z]?\d+(?:\.\d+)?[FT]?\d*)\s+', line)
                    if ref_match:
                        print(f"✓ Found product line {len(product_line_indices)}: {ref_match.group(1)}")
            
            print(f"Total product lines found: {len(product_line_indices)}")
            
            # Step 2: สำหรับแต่ละ product line ให้รวมข้อมูลจากบรรทัดถัดไป
            for i, line_index in enumerate(product_line_indices):
                current_line = lines[line_index].strip()
                
                # รวม description จากบรรทัดถัดไป (ถ้ามี)
                full_description = self._get_full_description(lines, line_index)
                
                # ✅ Parse product ด้วย description ที่สมบูรณ์
                product = self._parse_enhanced_product_line(current_line, full_description)
                
                if product:
                    products.append(product)
                    print(f"✅ Extracted: {product['ref']} - {product['product_type'][:60]}...")
                else:
                    # ✅ เพิ่ม debug เมื่อ parse ไม่สำเร็จ
                    ref_match = re.match(r'^([DW][A-Z]?\d+(?:\.\d+)?[FT]?\d*)\s+', current_line)
                    if ref_match:
                        print(f"❌ Failed to parse: {ref_match.group(1)} - Line: {current_line[:80]}...")
            
            print(f"Total products extracted: {len(products)}")
            
        except Exception as e:
            print(f"Error in enhanced product extraction: {str(e)}")
            import traceback
            traceback.print_exc()
        
        return products

    def _is_main_product_line(self, line: str) -> bool:
        """
        ตรวจสอบว่าเป็นบรรทัดหลักของ product
        รองรับรหัสแบบ 3/16, 2/14 (ชั้น/จำนวนหน้าต่าง) แต่ไม่จับ 1104/314 (ที่อยู่)
        ✅ เพิ่มรองรับ GRANT Series (W6, ADD1, ADD2, etc.)
        """
        patterns = [
            # ============================================================
            # ✅ GRANT Series - เพิ่มส่วนนี้ก่อนทุก pattern อื่น
            # ============================================================
            r'^W\d+\s+GRANT\b',              # W6 GRANT, W11 GRANT, W1 GRANT
            r'^W\d+\.\d+\s+GRANT\b',         # W11.1 GRANT, W6.5 GRANT
            r'^W\d+F\s+GRANT\b',             # W6F GRANT
            r'^W\d+F\d+\s+GRANT\b',          # W6F1 GRANT, W6F2 GRANT, W4F1 GRANT
            r'^W\d+T\s+GRANT\b',             # W6T GRANT
            r'^W\d+T\d+\s+GRANT\b',          # W6T1 GRANT, W6T2 GRANT
            
            r'^D\d+\s+GRANT\b',              # D1 GRANT, D3 GRANT
            r'^D\d+F\s+GRANT\b',             # D1F GRANT
            r'^D\d+F\d+\s+GRANT\b',          # D1F1 GRANT
            r'^D\d+T\s+GRANT\b',             # D1T GRANT
            r'^D\d+T\d+\s+GRANT\b',          # D1T1 GRANT
            
            r'^ADD\d+\s+GRANT\b',            # ADD1 GRANT, ADD2 GRANT
            r'^ADD\d+F\s+GRANT\b',           # ADD1F GRANT (ถ้ามี)

            # ============================================================
            # ✅ AD Series (Airflow Door) - AD1, AD2, AD1F, AD1T
            # ============================================================
            r'^AD\d+\.\d+\s+',               # AD1.5
            r'^AD\d+[FT]\d+\s+',             # AD1F1, AD1T2
            r'^AD\d+[FT]\s+',                # AD1F, AD1T
            r'^AD\d+\s+',                    # AD1 WE-70, AD2 ATIS

            # ============================================================
            # ✅ Generic ref + known series (catch-all สำหรับรหัสใหม่ๆ)
            #    เช่น SD1 WE-70, FD2 ATIS - anchored ด้วยชื่อ series
            #    จึงไม่ไปจับที่อยู่หรือบรรทัดหมายเหตุ
            # ============================================================
            r'^[A-Z]{1,3}\d+(?:\.\d+)?(?:[FT]\d*)?\s+(?:WE-|WD-|ATIS|Giesta|FW-G|GRANT)',

            # ============================================================
            # รหัสแบบ ชั้น/จำนวนหน้าต่าง (1-2 หลัก / 1-3 หลัก)
            # ============================================================
            r'^\d{1,2}/\d{1,3}\s+',                # 3/16, 2/14, 1/10 (ไม่จับ 1104/314)
            r'^\d{1,2}/\d{1,3}[FT]\s+',            # 3/16F, 2/14T
            r'^\d{1,2}/\d{1,3}F\d+\s+',            # 3/16F1, 3/16F2
            r'^\d{1,2}/\d{1,3}T\d+\s+',            # 3/16T1, 2/14T2
            
            # ============================================================
            # เพิ่ม pattern สำหรับ D1 หน้า, D2 หน้า, D3 ข้าง เป็นต้น
            # ============================================================
            r'^D\d+\s+หน้า\s+ATIS',              # D1 หน้า ATIS
            r'^D\d+\s+ข้าง\s+ATIS',              # D3 ข้าง ATIS
            r'^D\d+\s+หลัง\s+ATIS',              # D6 หลัง ATIS
            r'^D\d+\s+บันได\s+ATIS',            # D7 บันได ATIS
            r'^D\d+\s+[ก-๙]+\s+ATIS',            # D# <Thai text> ATIS (generic)
            
            r'^W\d+\s+หน้า\s+ATIS',              # W1 หน้า ATIS
            r'^W\d+\s+ข้าง\s+ATIS',              # W3 ข้าง ATIS
            r'^W\d+\s+[ก-๙]+\s+ATIS',            # W# <Thai text> ATIS (generic)
            
            # เพิ่มสำหรับ WE series ด้วย
            r'^D\d+\s+หน้า\s+WE-\d+',            # D1 หน้า WE-70
            r'^D\d+\s+ข้าง\s+WE-\d+',            # D3 ข้าง WE-70
            r'^D\d+\s+[ก-๙]+\s+WE-\d+',          # D# <Thai text> WE-70
            
            r'^W\d+\s+หน้า\s+WE-\d+',            # W1 หน้า WE-70
            r'^W\d+\s+ข้าง\s+WE-\d+',            # W3 ข้าง WE-70
            r'^W\d+\s+[ก-๙]+\s+WE-\d+',          # W# <Thai text> WE-70
            
            # ============================================================
            # WE Series (WE-70, WE-55, etc.)
            # ============================================================
            r'^[DW][A-Z]?\d+\s+WE-\d+',           # D1 WE-70, W02 WE-55
            r'^[DW][A-Z]?\d+F\s+WE-\d+',          # D1F WE-70
            r'^[DW][A-Z]?\d+F\d+\s+WE-\d+',       # D1F1 WE-70, D1F2 WE-70
            r'^[DW][A-Z]?\d+T\s+WE-\d+',          # D1T WE-70 (Transom)
            r'^[DW][A-Z]?\d+T\d+\s+WE-\d+',       # D1T1 WE-70
            r'^[DW][A-Z]?\d+\.\d+\s+WE-\d+',      # D1.5 WE-70
            r'^[DW][A-Z]?\d+\.\d+F\s+WE-\d+',     # D1.5F WE-70
            r'^[DW][A-Z]?\d+\.\d+F\d+\s+WE-\d+',  # D1.5F1 WE-70
            r'^[DW][A-Z]?\d+\.\d+T\s+WE-\d+',     # D1.5T WE-70
            
            # ============================================================
            # ATIS Series
            # ============================================================
            r'^[DW][A-Z]?\d+\s+ATIS',             # D1 ATIS, W02 ATIS
            r'^[DW][A-Z]?\d+F\s+ATIS',            # D1F ATIS
            r'^[DW][A-Z]?\d+F\d+\s+ATIS',         # D1F1 ATIS, D1F2 ATIS
            r'^[DW][A-Z]?\d+T\s+ATIS',            # D1T ATIS (Transom)
            r'^[DW][A-Z]?\d+T\d+\s+ATIS',         # D1T1 ATIS, D1T2 ATIS
            r'^[DW][A-Z]?\d+\.\d+\s+ATIS',        # D1.5 ATIS
            r'^[DW][A-Z]?\d+\.\d+F\s+ATIS',       # D1.5F ATIS
            r'^[DW][A-Z]?\d+\.\d+F\d+\s+ATIS',    # D1.5F1 ATIS
            r'^[DW][A-Z]?\d+\.\d+T\s+ATIS',       # D1.5T ATIS
            
            # ============================================================
            # Giesta Series
            # ============================================================
            r'^[DW][A-Z]?\d+\s+Giesta',           # D1 Giesta
            r'^[DW][A-Z]?\d+F\s+FW-G',            # D1F FW-G (Giesta Fixed)
            r'^[DW][A-Z]?\d+F\d+\s+Giesta',       # D1F1 Giesta
            r'^[DW][A-Z]?\d+F\d+\s+FW-G',         # D1F1 FW-G
            
            # ============================================================
            # WE-Plus Series
            # ============================================================
            r'^[DW][A-Z]?\d+\s+WE-Plus',          # D1 WE-Plus
            r'^[DW][A-Z]?\d+F\s+WE-Plus',         # D1F WE-Plus
            r'^[DW][A-Z]?\d+F\d+\s+WE-Plus',      # D1F1 WE-Plus
            r'^[DW][A-Z]?\d+T\s+WE-Plus',         # D1T WE-Plus
            r'^[DW][A-Z]?\d+T\d+\s+WE-Plus',      # D1T1 WE-Plus
            
            # ============================================================
            # WD Series (Window-Door combinations)
            # ============================================================
            r'^[DW][A-Z]?\d+\s+WD-\d+',           # D1 WD-70
            r'^[DW][A-Z]?\d+F\s+WD-\d+',          # D1F WD-70
            r'^[DW][A-Z]?\d+F\d+\s+WD-\d+',       # D1F1 WD-70
            
            # ============================================================
            # Generic pattern (สำหรับ series อื่นๆ ที่อาจมี)
            # ============================================================
            r'^[DW]?[A-Z]+-[A-Z]+\s+',            # Generic: DA-XX, WA-XX

            # ============================================================
            # รหัสที่มีจุดทศนิยม พร้อม F/T และตัวเลขต่อท้าย
            # เช่น W02.1F, W02.1F1, D1.5F, D1.5F1
            # ============================================================
            r'^W\d+\.\d+F\d+\s+',     # W02.1F1, W10.1F2, W3.5F1
            r'^D\d+\.\d+F\d+\s+',     # D1.5F1, D2.1F2
            r'^W\d+\.\d+T\d+\s+',     # W02.1T1, W10.1T2 (Transom)
            r'^D\d+\.\d+T\d+\s+',     # D1.5T1, D2.1T2
            r'^W\d+\.\d+F\s+',        # W02.1F, W10.1F, W3.5F
            r'^D\d+\.\d+F\s+',        # D1.5F, D2.1F
            r'^W\d+\.\d+T\s+',        # W02.1T, W10.1T (Transom)
            r'^D\d+\.\d+T\s+',        # D1.5T, D2.1T
            r'^W\d+\.\d+\s+',         # W02.1, W10.1, W3.5 (ไม่มี F/T)
            r'^D\d+\.\d+\s+',         # D1.5, D2.1 (ไม่มี F/T)
        ]
        
        # ตรวจสอบทุก pattern
        for pattern in patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
        
        return False

    def _get_full_description(self, lines: List[str], start_index: int) -> str:
        """รวม description จากหลายบรรทัด - ปรับปรุงให้ครอบคลุมมากขึ้น"""
        try:
            current_line = lines[start_index].strip()
            full_description = ""
            
            # แยกส่วน description จากบรรทัดหลัก
            # Pattern: D1 WE-70 [description part] 2350 2070 1 22,200.00 22,200.00
            main_desc_match = re.search(r'WE-\d+\s+(.*?)(?=\s+\d{3,4}\s+\d{3,4})', current_line)
            if main_desc_match:
                full_description = main_desc_match.group(1).strip()
            
            # ตรวจสอบบรรทัดถัดไปเพื่อหา description ต่อ
            next_index = start_index + 1
            continuation_found = False
            
            while next_index < len(lines):
                next_line = lines[next_index].strip()
                
                # หยุดถ้าเจอบรรทัดใหม่ที่เป็น product line
                if self._is_main_product_line(next_line):
                    break
                
                # หยุดถ้าเจอบรรทัดที่มีตัวเลขขนาด (เช่น 2350 2070)
                if re.search(r'\d{3,4}\s+\d{3,4}', next_line):
                    break
                
                # หยุดถ้าเจอบรรทัดว่างหรือบรรทัดที่ไม่เกี่ยวข้อง
                if not next_line or self._is_irrelevant_line(next_line):
                    break
                
                # หยุดถ้าเจอ separator line (____________)
                if re.match(r'^_{5,}', next_line):
                    break
                
                # ตรวจสอบว่าเป็น continuation line หรือไม่
                # ปรับเงื่อนไขให้ครอบคลุมมากขึ้น
                is_continuation = False
                
                # Pattern 1: ขึ้นต้นด้วยตัวเลขและ mm (เช่น "6mm")
                if re.match(r'^\d+\s*mm', next_line, re.IGNORECASE):
                    is_continuation = True
                
                # Pattern 2: เป็นส่วนต่อของคำที่ขาดจากบรรทัดก่อน (เช่น "6mm" ที่แยกมาจาก "+กระจกเขียวตัดแสง")
                elif not re.search(r'[A-Z]\d+', next_line) and len(next_line) < 50:
                    # ไม่มี code (W02, DA1) และความยาวไม่เกิน 50 ตัวอักษร
                    is_continuation = True
                
                # Pattern 3: มีคำเกี่ยวกับ product description
                elif any(keyword in next_line.lower() for keyword in 
                        ['door', 'window', 'sliding', 'panel', 'track', 'กระจก', 'มุ้ง', 
                        'casement', 'awning', 'fixed', 'swing', 'transom', 'mm', 'knock']):
                    # และไม่มีราคา
                    if not re.search(r'\d+,\d+\.00', next_line):
                        is_continuation = True
                
                if is_continuation:
                    # รวมเข้าไปใน description โดยเพิ่มช่องว่างคั่น
                    if full_description and not full_description.endswith(' '):
                        full_description += " "
                    full_description += next_line.strip()
                    continuation_found = True
                    next_index += 1
                else:
                    break
            
            # ทำความสะอาด description
            full_description = re.sub(r'\s+', ' ', full_description).strip()
            
            if continuation_found:
                print(f"📝 Combined description from {next_index - start_index} lines: '{full_description[:80]}...'")
            else:
                print(f"📝 Single line description: '{full_description[:80]}...'")
            
            return full_description
            
        except Exception as e:
            print(f"Error getting full description: {str(e)}")
            return ""

    def _is_continuation_line(self, line: str) -> bool:
        """ตรวจสอบว่าเป็นบรรทัดต่อของ description หรือไม่ - ปรับปรุงให้ครอบคลุมมากขึ้น"""
        line = line.strip()
        
        # ถ้าเป็นบรรทัดว่างหรือสั้นเกินไป
        if not line or len(line) < 2:
            return False
        
        # Pattern 1: ขึ้นต้นด้วยตัวเลขและ mm (เช่น "6mm")
        if re.match(r'^\d+\s*mm', line, re.IGNORECASE):
            return True
        
        # Pattern 2: มีคำที่บ่งบอกถึง description ต่อ
        continuation_patterns = [
            r'^down\)',       # "down)" ที่เหลือจาก "(Knock-down)"
            r'^\+',           # ขึ้นต้นด้วย "+"
            r'^กระจก',         # ขึ้นต้นด้วยคำว่า "กระจก"
            r'^mm',           # ขึ้นต้นด้วย "mm"
            r'^\d+mm',        # ขึ้นต้นด้วยตัวเลขตามด้วย mm
            r'^เขียว',         # เช่น "เขียวตัดแสง"
            r'^ตัดแสง',        # เช่น "ตัดแสง 6mm"
        ]
        
        for pattern in continuation_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                return True
        
        # ถ้าไม่มีตัวเลขขนาดใหญ่ (เช่น 2350, 2070) และไม่ใช่ราคา
        if not re.search(r'\d{3,4}', line) and not re.search(r'\d+,\d+\.00', line):
            # และมีคำที่เกี่ยวข้องกับ description
            if any(keyword in line.lower() for keyword in 
                ['panel', 'track', 'sliding', 'door', 'window', 'กระจก', 'แสง', 'mm', 'knock', 'down']):
                return True
        
        # ถ้าความยาวสั้น (< 30 ตัวอักษร) และไม่มี code หรือราคา
        if len(line) < 30:
            if not re.search(r'^[A-Z]\d+', line) and not re.search(r'\d+,\d+\.00', line):
                return True
        
        return False

    def _is_irrelevant_line(self, line: str) -> bool:
        """ตรวจสอบว่าเป็นบรรทัดที่ไม่เกี่ยวข้องหรือไม่"""
        line = line.strip()
        
        # บรรทัดที่ไม่เกี่ยวข้อง
        irrelevant_patterns = [
            r'^Code\s+Series',  # Header
            r'^Size\s+\(mm\.\)',  # Header
            r'^W\s+H\s+Qty',   # Header
            r'^รวมทั้งหมด',      # Summary
            r'^หมายเหตุ',       # Notes
            r'^-\s+',          # List items
            r'^\d+%',          # Percentage
            r'^ราคา',          # Price lines
            r'^ส่วนลด',        # Discount lines
        ]
        
        for pattern in irrelevant_patterns:
            if re.search(pattern, line):
                return True
        
        return False
    
    def _extract_ref_from_line(self, line: str) -> Optional[str]:
        """
        แยกหา ref จากบรรทัด - รองรับหลาย pattern แต่ไม่จับที่อยู่
        ✅ เพิ่มรองรับ ADD1, ADD2, W6, W11, etc.
        """
        patterns = [
            # ต้องเรียงจากเฉพาะเจาะจง → กว้าง
            r'^(ADD\d+)\b',                      # ADD1, ADD2 ← ต้องมาก่อน AD\d+
            r'^(AD\d+\.\d+)\b',                  # AD1.5
            r'^(AD\d+F\d+)\b',                   # AD1F1
            r'^(AD\d+T\d+)\b',                   # AD1T1
            r'^(AD\d+F)\b',                      # AD1F
            r'^(AD\d+T)\b',                      # AD1T
            r'^(AD\d+)\b',                       # AD1, AD2 (Airflow Door)
            r'^(W\d+\.\d+[FT]\d+)\b',            # W02.1F1, W02.1T2 ← ต้องมาก่อน W\d+\.\d+
            r'^(D\d+\.\d+[FT]\d+)\b',            # D1.5F1, D1.5T2
            r'^(W\d+\.\d+[FT])\b',               # W02.1F, W02.1T
            r'^(D\d+\.\d+[FT])\b',               # D1.5F, D1.5T
            r'^(W\d+\.\d+)\b',                   # W11.1 ← ย้ายมาก่อน!
            r'^(D\d+\.\d+)\b',                   # D1.5
            r'^(W\d+F\d+)\b',                    # W6F1 ← มาก่อน W\d+F
            r'^(D\d+F\d+)\b',                    # D1F1
            r'^(W\d+T\d+)\b',                    # W6T1 ← มาก่อน W\d+T
            r'^(D\d+T\d+)\b',                    # D1T1
            r'^(W\d+F)\b',                       # W6F
            r'^(D\d+F)\b',                       # D1F
            r'^(W\d+T)\b',                       # W6T
            r'^(D\d+T)\b',                       # D1T
            r'^(W\d+)\b',                        # W6, W11 ← มาหลังสุด!
            r'^(D\d+)\b',                        # D1, D3

            # Generic ref + known series (catch-all สำหรับรหัสใหม่ๆ เช่น SD1, FD2)
            r'^([A-Z]{1,3}\d+(?:\.\d+)?(?:[FT]\d*)?)\s+(?:WE-|WD-|ATIS|Giesta|FW-G|GRANT)',

            # รหัสแบบ ชั้น/จำนวนหน้าต่าง
            r'^(\d{1,2}/\d{1,3}[FT]?\d*)\b',
            r'\b(\d{1,2}/\d{1,3}[FT]?\d*)(?=\s)',
            
            # Generic patterns
            r'^([DW][A-Z]?\d+(?:\.\d+)?[FT]?\d*)\b',
            r'\b([DW][A-Z]?\d+(?:\.\d+)?[FT]?\d*)$',
            r'[_\-\s]([DW][A-Z]?\d+(?:\.\d+)?[FT]?\d*)[_\-\s]',
            r'\b([DW][A-Z]?\d+(?:\.\d+)?[FT]?\d*)\b',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                ref = match.group(1).upper()
                
                # ✅ ตรวจสอบเพิ่มเติม: ไม่ให้จับที่อยู่ (ตัวเลข 3-4 หลักก่อน /)
                if '/' in ref:
                    before_slash = ref.split('/')[0]
                    # ถ้าตัวเลขก่อน / มีมากกว่า 2 หลัก = เป็นที่อยู่
                    if len(before_slash) > 2:
                        continue
                
                return ref
        
        return None

    def _parse_enhanced_product_line(self, main_line: str, full_description: str) -> Optional[Dict[str, Any]]:
        """
        Parse product line - รองรับทุก series
        ✅ เพิ่มรองรับ GRANT Series
        """
        try:
            product = {
                'ref': '', 'series': '', 'product_type': '',
                'width': 0, 'height': 0, 'qty': 1,
                'color': '', 'glass': '', 'insect_screen': 'No',
                'remarks': '', 'opening_size': {'width': 0, 'height': 0},
                'price_unit': 0, 'price_total': 0
            }
            
            # หา Ref
            ref = self._extract_ref_from_line(main_line)
            if ref:
                product['ref'] = ref
                print(f"  📋 Found ref: {product['ref']}")
            else:
                print(f"  ❌ No ref found in line: {main_line[:80]}")
                return None
            
            # ============================================================
            # ✅ หา Series - เพิ่ม GRANT ด้วย
            # ============================================================
            series_patterns = [
                (r'(GRANT)', 'grant'),               # ✅ เพิ่ม GRANT
                (r'(ATIS)', 'atis'),
                (r'(WE-\d+)', 'we'),
                (r'(WD-\d+)', 'wd'),
                (r'(Giesta)', 'giesta'),
                (r'(FW-G)', 'fw-g'),
                (r'(WE-Plus)', 'we-plus'),
            ]
            
            series_type = 'grant'  # Default เป็น grant
            for pattern, s_type in series_patterns:
                series_match = re.search(pattern, main_line, re.IGNORECASE)
                if series_match:
                    product['series'] = series_match.group(1)
                    series_type = s_type
                    print(f"  📋 Found series: {product['series']}")
                    break
            
            if not product['series']:
                print(f"  ⚠️ No series found for {product['ref']}")

            # ===== ขั้นตอนที่ 1: รวม main_line + full_description =====
            combined_text = main_line
            if full_description and len(full_description) > 5:
                combined_text = main_line + " " + full_description
            
            # ===== ขั้นตอนที่ 2: Extract Glass ก่อนเสมอ! =====
            glass_info = self._extract_glass_info_enhanced(combined_text)
            product['glass'] = glass_info
            if glass_info:
                print(f"  🔍 Found glass: {glass_info[:60]}...")
            else:
                print(f"  ⚠️ No glass found")
            
            # ===== ขั้นตอนที่ 3: Extract Product Type (หลังจาก extract glass แล้ว) =====
            raw_description = ""
            
            if product['series']:
                # ลองหา description หลัง series จนถึงก่อน "Glass T:" หรือก่อนตัวเลขขนาด
                desc_pattern = rf"{re.escape(product['series'])}\s+(.*?)(?:\s*\+(?:LM|IGU|Glass|กระจก)|\s+\d{{3,4}}\s+\d{{3,4}}|\s*$)"
                desc_match = re.search(desc_pattern, main_line, re.IGNORECASE)
                
                if desc_match:
                    raw_description = desc_match.group(1).strip()
                    print(f"  🔍 Extracted description: {raw_description[:80]}...")
                else:
                    # ถ้าไม่เจอ ลองหาแบบง่ายๆ
                    desc_pattern2 = rf"{re.escape(product['series'])}\s+(.*?)(?=\s+\d{{3,4}}\s+\d{{3,4}}|\s*$)"
                    desc_match2 = re.search(desc_pattern2, main_line, re.IGNORECASE)
                    if desc_match2:
                        raw_description = desc_match2.group(1).strip()
                        print(f"  🔍 Extracted (simple): {raw_description[:80]}...")
            
            # ถ้ายังไม่ได้ description และมี full_description
            if (not raw_description or len(raw_description) < 10) and full_description:
                if not re.match(r'^\d+\s*mm', full_description) and not full_description.startswith('KD+'):
                    if any(keyword in full_description.lower() for keyword in 
                        ['window', 'door', 'awning', 'casement', 'fix', 'sliding', 'swing']):
                        raw_description = full_description.strip()
                        print(f"  🔍 Using full desc: {raw_description[:80]}...")
            
            # ถ้าเป็น Fixed window แต่ไม่มี description
            if not raw_description or len(raw_description) < 3:
                if product['ref'].endswith('F') or 'F' in product['ref'][-3:]:
                    raw_description = f"Fixed window for {product['series']}"
                    print(f"  🔍 Using default Fixed: {raw_description}")
                else:
                    raw_description = f"{product['ref']} {product['series']} Window/Door"
                    print(f"  🔍 Using default: {raw_description}")
            
            # ===== ขั้นตอนที่ 4: ลบส่วนกระจกออกจาก product_type =====
            product_type_clean = raw_description
            
            # ลบ Glass T: ... ออก
            product_type_clean = re.sub(r'Glass\s+T:\s*[^\s].*?(?=\s+\d{3,4}|\s*$)', '', product_type_clean, flags=re.IGNORECASE)
            
            # ลบ +กระจก... ออก
            product_type_clean = re.sub(r'\+\s*กระจก[^\+\n]*', '', product_type_clean, flags=re.IGNORECASE)
            
            # ลบ +Glass... ออก
            product_type_clean = re.sub(r'\+\s*Glass[^\+\n]*', '', product_type_clean, flags=re.IGNORECASE)
            
            # ลบ +KD... ออก
            product_type_clean = re.sub(r'\+\s*KD[^\+\n]*', '', product_type_clean, flags=re.IGNORECASE)
            
            # ลบ +LM Solar... ออก
            product_type_clean = re.sub(r'\+\s*LM\s+Solar[^\+\n]*', '', product_type_clean, flags=re.IGNORECASE)
            
            # ลบ +Solar... ออก
            product_type_clean = re.sub(r'\+\s*Solar[^\+\n]*', '', product_type_clean, flags=re.IGNORECASE)
            
            # ทำความสะอาดเพิ่มเติม
            product_type_clean = re.sub(r'\s+', ' ', product_type_clean).strip()
            product_type_clean = product_type_clean.rstrip('+').strip()
            
            # ลบ "KD+..." ที่อาจเหลืออยู่
            if product_type_clean.startswith('KD+'):
                product_type_clean = ''
            
            # ถ้าหลังจากลบแล้วไม่เหลืออะไร
            if len(product_type_clean) < 3:
                if product['ref'].endswith('F') or 'F' in product['ref'][-3:]:
                    product_type_clean = f"Fixed window for {product['series']}"
                else:
                    product_type_clean = f"{product['ref']} {product['series']} Window/Door"
            
            product['product_type'] = product_type_clean
            print(f"  🔍 Product type: {product_type_clean[:80]}...")
            
            # หาขนาด
            size_matches = re.findall(r'\b(\d{3,4})\b', main_line)
            if len(size_matches) >= 2:
                try:
                    product['width'] = int(size_matches[0])
                    product['height'] = int(size_matches[1])
                    print(f"  📏 Size: {product['width']}x{product['height']}")
                except:
                    pass
            
            # หา Qty
            qty_match = re.search(r'\s(\d+)\s+[\d,]+\.00', main_line)
            if qty_match:
                product['qty'] = int(qty_match.group(1))
                print(f"  🔢 Qty: {product['qty']}")
            
            # หาราคา
            price_matches = re.findall(r'([\d,]+\.00)', main_line)
            if len(price_matches) >= 1:
                try:
                    product['price_unit'] = float(price_matches[0].replace(',', ''))
                    print(f"  💰 Price: {product['price_unit']}")
                except:
                    pass
            if len(price_matches) >= 2:
                try:
                    product['price_total'] = float(price_matches[1].replace(',', ''))
                except:
                    pass
            
            # ตรวจสอบ Insect Screen
            product['insect_screen'] = self._check_insect_screen_enhanced(raw_description)
        
            if not product['ref']:
                return None
            
            print(f"✅ Parsed complete: {product['ref']}")
            
            return product
            
        except Exception as e:
            print(f"❌ Error parsing line: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_glass_info_enhanced(self, description: str) -> str:
        """แยกข้อมูลกระจกจาก description ที่สมบูรณ์ - เวอร์ชันที่แม่นยำ"""
        if not description:
            return ""
        
        print(f"  🔍 Trying to extract glass from: '{description[:100]}'")
        
        # ✅ Pattern ที่เจาะจงมากขึ้น - ต้องมี mm ด้วย
        glass_patterns = [
            # Pattern 1: Glass T: ... จนถึง mm
            (r'Glass\s+T:\s*([^+\n]*?\d+\.?\d*\s*mm)', 'glass_t_mm'),
            
            # Pattern 2: +กระจก...mm (หยุดที่ mm)
            (r'\+\s*((?:KD\+)?กระจก[^+\n]*?\d+\.?\d*\s*mm)', 'thai_plus_mm'),
            
            # Pattern 3: +Glass...mm (หยุดที่ mm)
            (r'\+\s*((?:KD\+)?Glass[^+\n]*?\d+\.?\d*\s*mm)', 'eng_plus_mm'),
            
            # Pattern 4: +LM Acoustic/Solar (เพิ่มเติม)
            (r'\+\s*(LM\s+(?:Acoustic|Solar)[^+\n]*?\d+\.?\d*\s*mm)', 'lm_plus_mm'),
            
            # Pattern 5: +IGU (เพิ่มเติม)
            (r'\+\s*(IGU[^+\n]*?\d+\.?\d*\s*mm)', 'igu_plus_mm'),
            
            # Pattern 6: กระจก...mm โดยตรง (ไม่มี +)
            (r'(?:^|\s)((?:KD\+)?กระจก[^+\n]*?\d+\.?\d*\s*mm)', 'thai_direct_mm'),
            
            # Pattern 7: Glass...mm โดยตรง (ไม่มี +)
            (r'(?:^|\s)((?:KD\+)?Glass[^+\n]*?\d+\.?\d*\s*mm)', 'eng_direct_mm'),
            
            # Pattern 8: LM Acoustic/Solar โดยตรง
            (r'(?:^|\s)(LM\s+(?:Acoustic|Solar)[^+\n]*?\d+\.?\d*\s*mm)', 'lm_direct_mm'),
            
            # Pattern 9: IGU โดยตรง
            (r'(?:^|\s)(IGU[^+\n]*?\d+\.?\d*\s*mm)', 'igu_direct_mm'),
        ]
        
        for pattern, pattern_type in glass_patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                glass_info = match.group(1).strip()
                
                print(f"  🔍 Matched pattern ({pattern_type}): '{glass_info}'")
                
                # ลบ + หน้า
                glass_info = glass_info.lstrip('+').strip()
                
                # ลบ "Glass T:" ถ้ามี
                glass_info = re.sub(r'^Glass\s+T:\s*', '', glass_info, flags=re.IGNORECASE).strip()
                
                # ✅ ตรวจสอบว่าไม่มี product type keywords ปน
                invalid_keywords = [
                    'door', 'window', 'airflow', 'swing', 'casement', 
                    'awning', 'รวมมุ้ง', 'knock-down', 'L/R', 'R/L',
                    'single', 'double', 'fixed', 'terrace', 'sliding'
                ]
                
                has_invalid = False
                for keyword in invalid_keywords:
                    if re.search(rf'\b{keyword}\b', glass_info, re.IGNORECASE):
                        print(f"  ❌ Invalid glass (contains '{keyword}'): '{glass_info[:60]}'")
                        has_invalid = True
                        break
                
                if has_invalid:
                    continue
                
                # ถ้าความยาวมากกว่า 80 ตัวอักษร = น่าจะผิด
                if len(glass_info) > 80:
                    print(f"  ❌ Invalid glass (too long: {len(glass_info)} chars)")
                    continue
                
                # ✅ ต้องมี mm
                if not re.search(r'\d+\s*mm', glass_info, re.IGNORECASE):
                    print(f"  ❌ Invalid glass (no mm found)")
                    continue
                
                # Clean ข้อมูลเพิ่มเติม
                glass_info = self._clean_glass_text_v2(glass_info)
                
                if len(glass_info) > 3:
                    print(f"  ✅ Extracted glass ({pattern_type}): '{glass_info}'")
                    return glass_info
        
        print(f"  ⚠️ No valid glass pattern found in description")
        return ""

    def _clean_glass_text_v2(self, glass_text: str) -> str:
        """ทำความสะอาดข้อความ glass - เวอร์ชัน 2 ที่เข้มงวดมากขึ้น"""
        if not glass_text:
            return ""
        
        text = glass_text.strip()
        
        print(f"    🧹 Cleaning glass: '{text}'")
        
        # ลบราคา (รูปแบบ: 22,400.00)
        text = re.sub(r'\d{1,3}(,\d{3})+\.\d{2}', '', text)
        
        # ลบคำว่า "ราคา" และตัวเลขที่ตามมา
        text = re.sub(r'ราคา[^\s]*', '', text, flags=re.IGNORECASE)
        
        # ลบตัวเลข 4+ หลักที่ไม่ใช่ mm (เช่น 8380, 2000, 1000)
        text = re.sub(r'\s+(\d{4,})(?!\s*mm)\b', '', text)
        
        # ✅ ลบตัวเลข 1-3 หลักที่ไม่มี mm ตามหลัง (เช่น "1 " ใน "กระจกเขียวตัดแสง 1 Airflow")
        # แต่เก็บตัวเลขที่มี mm (เช่น "6mm")
        text = re.sub(r'\s+(\d{1,3})(?!\s*mm)\s+', ' ', text)
        
        # ลบตัวอักษรไทยซ้ำ
        text = re.sub(r'([ก-๙])\1{2,}', r'\1', text)
        
        # ✅ ลบ product type keywords อย่างเข้มงวด
        keywords_to_remove = [
            r'\b[Dd]oor\b', r'\b[Ww]indow\b', r'\b[Aa]irflow\b', 
            r'\b[Ss]wing\b', r'\b[Cc]asement\b', r'\b[Aa]wning\b',
            r'รวมมุ้งแล้ว', r'\([Kk]nock-?down\)', r'[Kk]nock-?down',
            r'\bL/R\b', r'\bR/L\b', r'\b[Ss]ingle\b', r'\b[Dd]ouble\b',
            r'\b[Ff]ixed\b', r'\b[Tt]errace\b', r'\b[Ss]liding\b'
        ]
        
        for keyword in keywords_to_remove:
            text = re.sub(keyword, ' ', text, flags=re.IGNORECASE)
        
        # ลบช่องว่างซ้ำ
        text = re.sub(r'\s+', ' ', text).strip()
        
        # ลบ + ที่ติดหน้าหรือหลัง
        text = text.strip('+').strip()
        
        # ลบ ( ) ที่ว่างเปล่า
        text = re.sub(r'\(\s*\)', '', text)
        
        # Clean ซ้ำอีกครั้ง
        text = re.sub(r'\s+', ' ', text).strip()
        
        print(f"    🧹 Cleaned result: '{text}'")
        
        return text

    def _check_insect_screen_enhanced(self, description: str) -> str:
        """ตรวจสอบมุ้งจาก description ที่สมบูรณ์"""
        if not description:
            return "No"
        
        # Keywords ที่บ่งบอกถึงมุ้ง
        mosquito_keywords = [
            r'\(มุ้ง\)',
            r'มุ้ง',
            r'mosquito',
            r'insect\s*screen',
            r'screen',
            r'\(ม\)',
        ]
        
        for keyword in mosquito_keywords:
            if re.search(keyword, description, re.IGNORECASE):
                return "Yes"
        
        return "No"

    def _extract_project_info_from_text(self, text: str) -> Dict[str, str]:
        """แยกข้อมูลโครงการจากข้อความ"""
        project_info = {
            'project_name': '',
            'customer_name': '',
            'address': '',
            'phone': '',
            'date': '',
            'quotation_id': '',
            'color': '' 
        }
        
        try:
            lines = text.split('\n')
            
            for line in lines:
                line = line.strip()
                
                # หา Quotation ID
                if 'TM-' in line and not project_info['quotation_id']:
                    match = re.search(r'TM-\d+', line)
                    if match:
                        project_info['quotation_id'] = match.group()
                
                # หาวันที่
                if 'Date' in line:
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                    if date_match:
                        project_info['date'] = date_match.group(1)
                
                # หาชื่อลูกค้า
                if line.startswith('Attn.'):
                    customer = line.replace('Attn.', '').strip()
                    # แยก customer name จาก Date ถ้ามีในบรรทัดเดียวกัน
                    if 'Date' in customer:
                        customer = customer.split('Date')[0].strip()
                    if customer:
                        project_info['customer_name'] = customer
                
                if 'Project' in line and 'สี' in line:
                    # แยกส่วน Project
                    project_match = re.search(r'Project\s+(.+?)\s+สี\s+(.+?)(?:\s+Quoted|$)', line)
                    if project_match:
                        project_info['project_name'] = project_match.group(1).strip()
                        raw_color = project_match.group(2).strip()
                        project_info['color'] = self._format_color_with_code(raw_color)
                        print(f"✓ Extracted Project: '{project_info['project_name']}'")
                        print(f"✓ Extracted Color: '{project_info['color']}'")
                    else:
                        # ลองอีก pattern หนึ่ง - แยกด้วย "สี"
                        parts = line.split('สี')
                        if len(parts) >= 2:
                            # ส่วนแรกคือ Project name
                            project_part = parts[0].replace('Project', '').strip()
                            project_info['project_name'] = project_part
                            
                            # ส่วนที่สองคือสี
                            color_part = parts[1].strip()
                            # ตัดส่วน "Quoted by" ออก ถ้าม
                            color_part = re.sub(r'\s+Quoted.*$', '', color_part).strip()
                            project_info['color'] = self._format_color_with_code(color_part)
                            
                            print(f"✓ Extracted Project: '{project_info['project_name']}'")
                            print(f"✓ Extracted Color: '{project_info['color']}'")

                # หาเบอร์โทร
                phone_match = re.search(r'(\d{3}-\d{3}-\d{4}|\d{10})', line)
                if phone_match and not project_info['phone']:
                    project_info['phone'] = phone_match.group(1)
                
                # หาที่อยู่ - มักจะอยู่ในบรรทัดที่มี "Addr."
                if line.startswith('Addr.'):
                    addr = line.replace('Addr.', '').strip()
                    # แยก address จาก Project ถ้ามีในบรรทัดเดียวกัน
                    if 'Project' in addr:
                        addr = addr.split('Project')[0].strip()
                    if addr:
                        project_info['address'] = addr
            
            if project_info['color']:
                print(f"\n🎨 Successfully extracted color: '{project_info['color']}'")
            else:
                print(f"\n⚠️ Warning: No color found in project info")
            
        except Exception as e:
            print(f"Project info extraction error: {str(e)}")
        
        return project_info
    
    def _format_color_with_code(self, color_text: str) -> str:
        """แปลงชื่อสีให้มีรหัสสีในวงเล็บ เช่น Natural White -> [P]Natural White"""
        if not color_text:
            return ""
        
        # รายการสี Aluminum และ Giesta พร้อมรหัส
        color_mapping = {
            # Aluminum colors
            'natural white': '[P]Natural White',
            'natural silver': '[D]Natural Silver',
            'ivory white': '[W]Ivory White',
            'shine grey': '[K]Shine Grey',
            'shine gray': '[K]Shine Grey',
            'autumn brown': '[G]Autumn Brown',
            'natural black': '[T]Natural Black',
            'dusk gray': '[U]Dusk Gray',
            
            # Giesta colors
            'turin pine': '[B]Turin Pine',
            'crea mocha': '[C]Crea Mocha',
            'crea rusk': '[F]Crea Rusk',
            'teak': '[J]Teak',
            'polish silver': '[D]Polish Silver',
            'silky white': '[H]Silky White',
        }
        
        # ทำความสะอาดและแปลงเป็น lowercase สำหรับเทียบ
        clean_color = color_text.strip().lower()
        
        # ตรวจสอบว่ามีรหัสอยู่แล้วหรือไม่
        if re.match(r'\[[A-Z]\]', color_text):
            return color_text
        
        # หาสีที่ตรงกัน
        for color_key, formatted_color in color_mapping.items():
            if color_key in clean_color or clean_color in color_key:
                print(f"   🎨 Formatted color: '{color_text}' -> '{formatted_color}'")
                return formatted_color
        
        # ถ้าไม่เจอในรายการ ให้คืนค่าเดิม
        print(f"   ⚠️  Color not in mapping: '{color_text}'")
        return color_text

    def _calculate_summary(self, products: List[Dict]) -> Dict[str, Any]:
        """คำนวดสรุปข้อมูล"""
        return {
            'total_items': len(products),
            'total_windows': len([p for p in products if p.get('ref', '').startswith('W')]),
            'total_doors': len([p for p in products if p.get('ref', '').startswith('D')]),
            'total_qty': sum(p.get('qty', 0) for p in products),
            'with_mosquito_net': len([p for p in products if p.get('insect_screen') == 'Yes']),
            'total_value': sum(p.get('price_total', 0) for p in products)
        }
    
# แก้ไขในฟังก์ชัน smart_mosquito_detection_and_merge
# ส่วนที่ต้องแก้คือตอนเก็บ Fixed window ลงใน Type2, Type3, Type4

def smart_mosquito_detection_and_merge(products: List[Dict]) -> List[Dict]:
    """
    จัดการการตรวจจับและรวมข้อมูลมุ้งอย่างชาญฉลาด
    - ถ้า ref เดียวกันมีทั้งแบบมีมุ้งและไม่มีมุ้ง ให้รวมเป็นตัวเดียวกันและ set insect_screen = "Yes"  
    - ถ้า ref ไหนไม่มี ref ที่เป็นมุ้ง ให้ insect_screen = "No"
    - ถ้า ref มี F ต่อท้าย (Fixed window) ให้แยกใส่ใน type2, type3, type4
    - ถ้า ref มี T ต่อท้าย (Transom) ให้ข้ามไป ไม่ต้องประมวลผล
    - ถ้ามีคำว่า "รวมมุ้งแล้ว" ให้ถือว่าเป็น main product ที่มีมุ้งในตัว
    - แยก qty ของแต่ละ product type ไม่รวมกัน
    - เพิ่ม (qty) ต่อท้าย product_type ถ้า qty > 1
    - แยก Fixed window แต่ละตัว (F1, F2, F3...) ไปใส่ type2, type3, type4
    - 🔥 เก็บข้อมูล height ของ Fixed window ไว้ใน group_details เพื่อใช้บวก H
    """
    print("🔍 Starting smart mosquito detection and merge with separate Fixed columns...")
    
    # จัดกลุ่ม products ตาม base ref (ไม่รวม F และ T)
    ref_groups = {}
    skipped_transom = []
    
    for product in products:
        ref = product.get('ref', '').strip().upper()
        if not ref:
            continue
        
        # ข้าม Transom (T ต่อท้าย) ทั้งหมด
        if ref.endswith('T') or re.search(r'T\d+$', ref):
            skipped_transom.append(ref)
            print(f"   ⏭️ Skipping Transom: {ref}")
            continue
        
        # 🔥 แก้ไข - ตรวจสอบ Fixed window อย่างถูกต้อง
        base_ref = ref
        is_fixed = False
        fixed_number = None  # เก็บหมายเลข F (1, 2, 3...)
        
        # Pattern 1: Fixed ที่มีหมายเลข (D1F1, D1F2, W02F3...)
        f_match = re.match(r'^(.+?)F(\d+)$', ref)
        if f_match:
            base_ref = f_match.group(1)
            fixed_number = int(f_match.group(2))
            is_fixed = True
            print(f"   🪟 Found Fixed window with number: {ref} -> base: {base_ref}, F{fixed_number}")
        
        # Pattern 2: Fixed ที่ไม่มีหมายเลข (D1F, W02F, DA1F...)
        elif ref.endswith('F'):
            base_ref = ref[:-1]  # เอา F ออก
            fixed_number = 1  # ถือว่าเป็น F1
            is_fixed = True
            print(f"   🪟 Found Fixed window: {ref} -> base: {base_ref}, F1")
        
        # สร้าง group ถ้ายังไม่มี
        if base_ref not in ref_groups:
            ref_groups[base_ref] = {
                'main_products': [],
                'fixed_products': {},  # ✅ เปลี่ยนเป็น dict เพื่อเก็บแยกตามหมายเลข
                'mosquito_products': []
            }
        
        # จัดหมวดหมู่ product
        product_type = product.get('product_type', '').lower()
        
        # ตรวจสอบว่าเป็น "รวมมุ้งแล้ว" (main product ที่มีมุ้งในตัว)
        has_integrated_mosquito = 'รวมมุ้งแล้ว' in product_type
        
        # ตรวจสอบว่าเป็น mosquito product หรือไม่
        mosquito_patterns = [
            r'\(มุ้ง\)',
            r'มุ้ง',
            r'mosquito',
            r'insect\s*screen',
            r'net',
            r'\(ม\)',
            r'screen'
        ]
        
        # เพิ่มเงื่อนไข: ถ้าไม่ใช่ "รวมมุ้งแล้ว" และมี pattern มุ้ง
        is_mosquito_product = False
        if not has_integrated_mosquito:
            for pattern in mosquito_patterns:
                if re.search(pattern, product_type, re.IGNORECASE):
                    is_mosquito_product = True
                    break
            
            # ตรวจสอบจาก field เดิมด้วย
            if product.get('insect_screen', '').lower() == 'yes':
                is_mosquito_product = True
        
        # ✅ จัดเก็บตามประเภท - Fixed แยกตามหมายเลข
        if is_fixed and fixed_number:
            # เก็บ Fixed แยกตามหมายเลข
            if fixed_number not in ref_groups[base_ref]['fixed_products']:
                ref_groups[base_ref]['fixed_products'][fixed_number] = []
            
            # 🔥 เก็บข้อมูลครบถ้วน รวมถึง height ด้วย
            fixed_product_data = {
                'ref': ref,
                'product_type': product.get('product_type', ''),
                'width': product.get('width', 0),
                'height': product.get('height', 0),  # 🔥 เก็บ height ไว้ด้วย!
                'qty': product.get('qty', 1),
                'series': product.get('series', ''),
                'glass': product.get('glass', ''),
                'color': product.get('color', ''),
            }
            
            ref_groups[base_ref]['fixed_products'][fixed_number].append(fixed_product_data)
            print(f"   ✅ Added to fixed_products[{fixed_number}]: {ref} (H={product.get('height', 0)}mm) - {product_type[:50]}...")
            
        elif is_mosquito_product:
            ref_groups[base_ref]['mosquito_products'].append(product)
            print(f"   🦟 Added to mosquito_products: {ref} - {product_type[:50]}...")
        else:
            # ถ้ามี "รวมมุ้งแล้ว" ให้เป็น main product และ set insect_screen = Yes
            ref_groups[base_ref]['main_products'].append(product)
            if has_integrated_mosquito:
                product['insect_screen'] = 'Yes'
                print(f"   🪟✓ Added to main_products (with mosquito): {ref} - {product_type[:50]}...")
            else:
                print(f"   🪟 Added to main_products: {ref} - {product_type[:50]}...")
    
    if skipped_transom:
        print(f"\n⏭️ Skipped {len(skipped_transom)} Transom products: {', '.join(skipped_transom)}")
    
    print(f"\n📊 Found {len(ref_groups)} unique base references")
    
    merged_products = []
    
    for base_ref, group_data in ref_groups.items():
        main_products = group_data['main_products']
        fixed_products_dict = group_data['fixed_products']  # ✅ Dict แทน List
        mosquito_products = group_data['mosquito_products']
        
        print(f"\n🔍 Processing base ref: {base_ref}")
        print(f"   Main: {len(main_products)}, Fixed groups: {len(fixed_products_dict)}, Mosquito: {len(mosquito_products)}")
        
        # กรณีที่ไม่มี main product เลย - ข้ามไป
        if not main_products:
            print(f"   ⚠️ No main product for {base_ref}, skipping...")
            continue
        
        # ใช้ main product แรกเป็นฐาน
        main_product = main_products[0].copy()

        # ตรวจสอบและคูณ width ถ้ามี (2), (3) ในชื่อ product
        if main_product.get('width', 0) > 0 and main_product.get('product_type'):
            panel_match = re.search(r'\((\d+)\)', main_product['product_type'])
            if panel_match:
                num_panels = int(panel_match.group(1))
                if num_panels > 1:
                    # ตรวจสอบว่า width ถูกคูณไปแล้วหรือยัง
                    current_width = main_product['width']
                    
                    # สมมติว่าถ้า width < 1500 และมี (2) แสดงว่ายังไม่ได้คูณ
                    expected_min_width = 1500 * num_panels
                    
                    # ถ้า width ปัจจุบันน้อยกว่าที่คาดไว้มาก แสดงว่ายังไม่ได้คูณ
                    if current_width < expected_min_width / 2:
                        original_width = current_width
                        main_product['width'] = current_width * num_panels
                        print(f"   📢 Re-multiplied width for {base_ref}: {original_width} × {num_panels} = {main_product['width']}")
        
        # ทำความสะอาด product_type และเพิ่ม (qty) ถ้า qty > 1
        main_type = main_product.get('product_type', '')
        main_type = re.sub(r'\(\s*Knock-?\s*down\s*\)', '', main_type, flags=re.IGNORECASE).strip()
        main_type = main_type.lstrip('+').strip()
        main_qty = main_product.get('qty', 1)
        
        # เพิ่ม (qty) ถ้า > 1
        if main_qty > 1:
            main_type = re.sub(r'\s*\(\d+\)\s*$', '', main_type).strip()
            main_type = f"{main_type} ({main_qty})"
        
        main_product['product_type'] = main_type
        main_product['qty'] = main_qty  # เก็บ qty เดิม ไม่รวม
        
        # ✅ แยก Fixed types ออกเป็น Type2, Type3, Type4
        if fixed_products_dict:
            # เรียงลำดับ Fixed number (1, 2, 3, 4...)
            sorted_fixed_numbers = sorted(fixed_products_dict.keys())
            
            print(f"   ✅ Found Fixed windows: F{sorted_fixed_numbers}")
            
            # รวม Fixed types ทั้งหมด
            all_fixed_types = []
            
            for fixed_num in sorted_fixed_numbers:
                fixed_products = fixed_products_dict[fixed_num]
                
                for fixed_prod in fixed_products:
                    fixed_type = fixed_prod.get('product_type', '')
                    fixed_type = re.sub(r'\(\s*Knock-?\s*down\s*\)', '', fixed_type, flags=re.IGNORECASE).strip()
                    fixed_type = fixed_type.lstrip('+').strip()
                    fixed_qty = fixed_prod.get('qty', 1)
                    
                    # เพิ่ม (qty) ถ้า > 1
                    if fixed_qty > 1:
                        fixed_type = re.sub(r'\s*\(\d+\)\s*$', '', fixed_type).strip()
                        fixed_type = f"{fixed_type} ({fixed_qty})"
                    
                    if fixed_type:
                        all_fixed_types.append(fixed_type)
            
            # ✅ ตรวจสอบว่ามี Fixed types กี่ตัว
            total_fixed = len(all_fixed_types)
            
            # ถ้ามี Fixed types ให้เพิ่ม " + " ต่อท้าย product_type
            if total_fixed > 0:
                current_main_type = main_product.get('product_type', '')
                if not current_main_type.endswith(' + '):
                    main_product['product_type'] = f"{current_main_type} + "
            
            # ✅ แยก Fixed types ไปยัง Type2, Type3, Type4
            # กฎ: ตัวสุดท้ายไม่มี " + " ตัวอื่นๆ มี " + "
            
            if total_fixed >= 1:
                # Type2
                if total_fixed == 1:
                    # ถ้ามีแค่ 1 ตัว (Type2 เป็นตัวสุดท้าย) ไม่ต้องมี +
                    main_product['Type2'] = all_fixed_types[0]
                else:
                    # ถ้ามีมากกว่า 1 ตัว ให้มี +
                    main_product['Type2'] = f"{all_fixed_types[0]} + "
                print(f"   ✅ Type2: '{main_product['Type2']}'")
            else:
                main_product['Type2'] = ''
            
            if total_fixed >= 2:
                # Type3
                if total_fixed == 2:
                    # ถ้ามี 2 ตัว (Type3 เป็นตัวสุดท้าย) ไม่ต้องมี +
                    main_product['Type3'] = all_fixed_types[1]
                else:
                    # ถ้ามีมากกว่า 2 ตัว ให้มี +
                    main_product['Type3'] = f"{all_fixed_types[1]} + "
                print(f"   ✅ Type3: '{main_product['Type3']}'")
            else:
                main_product['Type3'] = ''
            
            if total_fixed >= 3:
                # Type4 เป็นตัวสุดท้ายเสมอ ไม่ต้องมี +
                main_product['Type4'] = all_fixed_types[2]
                print(f"   ✅ Type4: '{main_product['Type4']}'")
            else:
                main_product['Type4'] = ''
            
            if total_fixed > 3:
                print(f"   ⚠️ Warning: Found {total_fixed} Fixed windows, only first 3 will be shown")
            
            main_product['has_fixed_window'] = True
            
            # 🔥 เก็บ group_details เพื่อใช้ดึง height ของ Fixed window
            all_fixed_details = []
            for fixed_num in sorted_fixed_numbers:
                for fixed_prod in fixed_products_dict[fixed_num]:
                    all_fixed_details.append(fixed_prod)
            
            main_product['group_details'] = main_products + all_fixed_details + mosquito_products
            
            print(f"   ✅ Main product_type: '{main_product['product_type'][:80]}'")
        else:
            main_product['Type2'] = ''
            main_product['Type3'] = ''
            main_product['Type4'] = ''
            main_product['has_fixed_window'] = False
            main_product['group_details'] = main_products + mosquito_products

        # ตรวจสอบและรวม mosquito
        if mosquito_products:
            main_product['insect_screen'] = 'Yes'
            main_product['merged_from_mosquito'] = True
            main_product['mosquito_products_count'] = len(mosquito_products)
            print(f"   ✅ Merged with mosquito net ({len(mosquito_products)} items)")
        else:
            # ถ้าไม่มี mosquito product แยก แต่ main product มี insect_screen = Yes อยู่แล้ว
            # (จากการที่มี "รวมมุ้งแล้ว") ก็ไม่ต้องเปลี่ยน
            if main_product.get('insect_screen') != 'Yes':
                main_product['insect_screen'] = 'No'
            main_product['merged_from_mosquito'] = False
        
        # รวม remarks
        all_products = main_products + [p for prods in fixed_products_dict.values() for p in prods] + mosquito_products
        all_remarks = []
        for p in all_products:
            if p.get('remarks'):
                all_remarks.append(p['remarks'])
        
        if all_remarks:
            main_product['combined_remarks'] = '; '.join(all_remarks)
        
        # เก็บข้อมูลการรวม
        main_product['total_products_merged'] = len(all_products)
        
        merged_products.append(main_product)
        
        print(f"   ✅ Created merged product: {base_ref}")
        print(f"      - Main Type: '{main_product.get('product_type', '')[:60]}'")
        print(f"      - Type2: '{main_product.get('Type2', '')[:60]}'")
        print(f"      - Type3: '{main_product.get('Type3', '')[:60]}'")
        print(f"      - Type4: '{main_product.get('Type4', '')[:60]}'")
        print(f"      - Has Fixed: {main_product.get('has_fixed_window', False)}")
        print(f"      - Insect Screen: {main_product.get('insect_screen', 'No')}")
        print(f"      - Main Qty: {main_product.get('qty', 1)}")
    
    total_original = len([p for p in products if not p.get('ref', '').upper().endswith('T')])
    
    print(f"\n✅ Smart merge completed: {total_original} → {len(merged_products)} products")
    print(f"🦟 Products with mosquito net: {len([p for p in merged_products if p.get('insect_screen') == 'Yes'])}")
    print(f"🪟 Products without mosquito net: {len([p for p in merged_products if p.get('insect_screen') == 'No'])}")
    print(f"🪟 Products with Fixed window: {len([p for p in merged_products if p.get('has_fixed_window')])}")
    print(f"⏭️ Skipped Transom: {len(skipped_transom)}")
    
    return merged_products

class EnhancedQuotationProcessor:
    """ปรับปรุงการประมวลผล Quotation ให้แม่นยำยิ่งขึ้น"""
    
    def __init__(self):
        self.quo_data = None
        self.processed_data = {}
        
        # Pattern สำหรับการแยกข้อมูล Glass
        self.glass_patterns = [
            r'\+\s*([^+\n]*กระจก[^+\n]*)',  # +กระจกเขียวตัดแสง 6mm
            r'กระจก[^+\n,]*(?:\d+\s*mm)?',   # กระจกเขียวตัดแสง 6mm
            r'Glass[^+\n,]*(?:\d+\s*mm)?',   # Glass variants
            r'แก้ว[^+\n,]*(?:\d+\s*mm)?',    # แก้ว variants
        ]
        
        # Keywords สำหรับ Insect Screen (มุ้ง)
        self.mosquito_keywords = [
            r'\(มุ้ง\)',        # (มุ้ง) ในวงเล็บ
            r'มุ้ง',           # มุ้ง ธรรมดา
            r'mosquito',       # mosquito
            r'insect\s*screen', # insect screen
            r'net',            # net
            r'\(ม\)',          # (ม) ตัวย่อ
            r'screen',         # screen
        ]

    def extract_glass_from_product_type(self, product_type: str) -> str:
        """แยกข้อมูลกระจกจาก product type อย่างละเอียด"""
        if not product_type:
            return ""
        
        # ลองแต่ละ pattern
        for pattern in self.glass_patterns:
            match = re.search(pattern, product_type, re.IGNORECASE)
            if match:
                glass_info = match.group(1) if '+' in pattern else match.group()
                # ทำความสะอาดข้อมูล
                glass_info = glass_info.strip()
                glass_info = re.sub(r'^\+\s*', '', glass_info)  # เอา + หน้าออก
                glass_info = re.sub(r'\s+', ' ', glass_info)    # รวม spaces
                
                if len(glass_info) > 3:  # ต้องมีข้อมูลจริง
                    return glass_info
        
        return ""

    def determine_insect_screen(self, product_type: str, remarks: str = "") -> str:
        """กำหนด insect screen โดยดูจาก keywords ต่างๆ"""
        text_to_check = f"{product_type} {remarks}".lower()
        
        # ตรวจสอบแต่ละ keyword
        for keyword_pattern in self.mosquito_keywords:
            if re.search(keyword_pattern, text_to_check, re.IGNORECASE):
                return "Yes"
        
        return "No"

    def extract_series_from_product_type(self, product_type: str) -> str:
        """แยก Series จาก product type"""
        if not product_type:
            return ""
        
        # Pattern สำหรับ Series เช่น WE-70, WE-55
        series_patterns = [
            r'WE-\d+',
            r'WD-\d+', 
            r'[A-Z]{2}-\d+',
        ]
        
        for pattern in series_patterns:
            match = re.search(pattern, product_type, re.IGNORECASE)
            if match:
                return match.group().upper()
        
        return ""

    def process_quotation_file(self, file_path: str) -> Dict[str, Any]:
        """ประมวลผลไฟล์ Quotation ที่ปรับปรุงแล้ว - รองรับ PDF พร้อม Smart Mosquito Detection"""
        try:
            file_ext = os.path.splitext(file_path)[1].lower()
            
            # ถ้าเป็น PDF ให้ใช้ TOSTEM processor
            if file_ext == '.pdf':
                if not PDF_SUPPORT:
                    raise ValueError("ไม่รองรับไฟล์ PDF (ขาด pdfplumber library)")
                
                tostem_processor = EnhancedTOSTEMQuotationProcessor()
                result = tostem_processor.process_tostem_quotation_pdf(file_path)
                
                # Apply smart mosquito detection
                if result['success'] and result['data'].get('products'):
                    print("\n🧠 Applying smart mosquito detection to PDF data...")
                    original_products = result['data']['products']
                    smart_merged_products = smart_mosquito_detection_and_merge(original_products)
                    
                    result['data']['products'] = smart_merged_products
                    result['data']['summary'] = self._calculate_smart_summary(smart_merged_products)
                    
                    mosquito_count = len([p for p in smart_merged_products if p.get('insect_screen') == 'Yes'])
                    result['message'] += f" | Smart detection: {mosquito_count} with mosquito net"
                
                if result['success']:
                    self.processed_data = result['data']
                
                return result
            
            # สำหรับไฟล์อื่นๆ ใช้วิธีเดิม
            if file_ext in ['.xlsx', '.xls']:
                df = pd.read_excel(file_path)
                self.quo_data = df
            elif file_ext == '.csv':
                df = pd.read_csv(file_path, encoding='utf-8')
                self.quo_data = df
            else:
                raise ValueError(f"ไม่รองรับไฟล์ประเภท {file_ext}")
            
            # ประมวลผลข้อมูล
            raw_products = self._extract_enhanced_products(df)
            
            # Apply smart mosquito detection
            print("\n🧠 Applying smart mosquito detection...")
            smart_merged_products = smart_mosquito_detection_and_merge(raw_products)
            
            self.processed_data = {
                'project_info': self._extract_project_info(df),
                'products': smart_merged_products,
                'summary': self._calculate_smart_summary(smart_merged_products)
            }
            
            mosquito_count = len([p for p in smart_merged_products if p.get('insect_screen') == 'Yes'])
            merged_count = len([p for p in smart_merged_products if p.get('merged_from_mosquito')])
            
            message = f'โหลดข้อมูลจาก Quotation สำเร็จ ({len(smart_merged_products)} รายการ)'
            if mosquito_count > 0:
                message += f' | มุ้ง: {mosquito_count} รายการ'
            if merged_count > 0:
                message += f' | Auto-merged: {merged_count} refs'
            
            return {
                'success': True,
                'data': self.processed_data,
                'message': message
            }
            
        except Exception as e:
            return {
                'success': False,
                'data': {},
                'message': f'เกิดข้อผิดพลาดในการโหลด Quotation: {str(e)}'
            }
    
    def _calculate_smart_summary(self, products: List[Dict]) -> Dict[str, Any]:
        """คำนวณสรุปข้อมูลสำหรับ smart merged products"""
        return {
            'total_items': len(products),
            'total_windows': len([p for p in products if 'W' in p.get('ref', '').upper()]),
            'total_doors': len([p for p in products if 'D' in p.get('ref', '').upper()]),
            'total_qty': sum(p.get('qty', 0) for p in products),
            'with_mosquito_net': len([p for p in products if p.get('insect_screen') == 'Yes']),
            'auto_merged_refs': len([p for p in products if p.get('merged_from_mosquito', False)])
        }

    def _extract_enhanced_products(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """แยกข้อมูลสินค้าอย่างละเอียด"""
        products = []
        
        print(f"DEBUG: Processing {len(df)} rows from quotation")
        print(f"DEBUG: Columns: {list(df.columns)}")
        
        for index, row in df.iterrows():
            product = {
                'ref': '',
                'series': '',
                'product_type': '',
                'width': 0,
                'height': 0,
                'qty': 1,
                'color': '',
                'glass': '',
                'insect_screen': 'No',
                'remarks': '',
                'opening_size': {'width': 0, 'height': 0},  # เพิ่ม opening size
                'price_unit': 0,
                'price_total': 0
            }
            
            # แยกข้อมูลจากแต่ละคอลัมน์
            for col, value in row.items():
                col_str = str(col).lower()
                value_str = str(value) if pd.notna(value) else ''
                
                if any(keyword in col_str for keyword in ['ref', 'รหัส', 'item', 'no']):
                    product['ref'] = value_str
                elif any(keyword in col_str for keyword in ['type', 'ประเภท', 'product', 'description', 'desc']):
                    product['product_type'] = value_str
                elif any(keyword in col_str for keyword in ['width', 'กว้าง', 'w']):
                    product['width'] = self._extract_number(value_str)
                elif any(keyword in col_str for keyword in ['height', 'สูง', 'h']):
                    product['height'] = self._extract_number(value_str)
                elif any(keyword in col_str for keyword in ['qty', 'จำนวน', 'quantity']):
                    product['qty'] = self._extract_number(value_str) or 1
                elif any(keyword in col_str for keyword in ['color', 'สี', 'colour']):
                    product['color'] = value_str
                elif any(keyword in col_str for keyword in ['glass', 'กระจก']):
                    product['glass'] = value_str
                elif any(keyword in col_str for keyword in ['remark', 'หมายเหตุ', 'note']):
                    product['remarks'] = value_str
                elif any(keyword in col_str for keyword in ['price', 'ราคา', 'cost']):
                    if 'unit' in col_str or 'หน่วย' in col_str:
                        product['price_unit'] = self._extract_number(value_str)
                    elif 'total' in col_str or 'รวม' in col_str:
                        product['price_total'] = self._extract_number(value_str)
            
            # ตรวจสอบและคูณ width ถ้ามี (2), (3) ฯลฯ
            if product['width'] > 0 and product['product_type']:
                panel_match = re.search(r'\((\d+)\)', product['product_type'])
                if panel_match:
                    num_panels = int(panel_match.group(1))
                    if num_panels > 1:
                        original_width = product['width']
                        product['width'] = original_width * num_panels
                        print(f"   🔢 Multiplied width for {product['ref']}: {original_width} × {num_panels} = {product['width']}")
            
            # ถ้า product_type มีข้อมูล ให้แยกข้อมูลเพิ่มเติม
            if product['product_type']:
                # แยก Series
                if not product.get('series'):
                    product['series'] = self.extract_series_from_product_type(product['product_type'])
                
                # แยก Glass
                if not product['glass']:
                    product['glass'] = self.extract_glass_from_product_type(product['product_type'])
                
                # กำหนด Insect Screen (จะถูกแก้ไขใน smart_mosquito_detection_and_merge ภายหลัง)
                product['insect_screen'] = self.determine_insect_screen(
                    product['product_type'], 
                    product['remarks']
                )
            
            # Debug output
            if product['ref']:
                print(f"DEBUG: Found product - Ref: {product['ref']}")
                print(f"DEBUG: Series: {product['series']}, Glass: {product['glass']}")
                print(f"DEBUG: Insect screen (before smart merge): {product['insect_screen']}")
                
                products.append(product)
        
        print(f"DEBUG: Extracted {len(products)} products from quotation (before smart merge)")
        return products

    def _extract_number(self, text: str) -> int:
        """แยกตัวเลขจากข้อความ"""
        try:
            # ลบ comma และ whitespace
            clean_text = re.sub(r'[,\s]', '', str(text))
            numbers = re.findall(r'\d+', clean_text)
            return int(numbers[0]) if numbers else 0
        except:
            return 0

    def _extract_project_info(self, df: pd.DataFrame) -> Dict[str, str]:
        """แยกข้อมูลโครงการ"""
        project_info = {
            'project_name': '',
            'customer_name': '',
            'address': '',
            'phone': '',
            'date': datetime.now().strftime('%Y-%m-%d'),
            'quotation_id': ''
        }
        
        # ค้นหาข้อมูลจากคอลัมน์ต่างๆ
        for col in df.columns:
            col_str = str(col).lower()
            if 'project' in col_str or 'โครงการ' in col_str:
                project_info['project_name'] = str(df[col].iloc[0]) if not df[col].empty else ''
            elif 'customer' in col_str or 'ลูกค้า' in col_str:
                project_info['customer_name'] = str(df[col].iloc[0]) if not df[col].empty else ''
            elif 'address' in col_str or 'ที่อยู่' in col_str:
                project_info['address'] = str(df[col].iloc[0]) if not df[col].empty else ''
            elif 'phone' in col_str or 'เบอร์' in col_str:
                project_info['phone'] = str(df[col].iloc[0]) if not df[col].empty else ''
        
        return project_info

    def _calculate_summary(self, products: List[Dict]) -> Dict[str, Any]:
        """คำนวดสรุปข้อมูล"""
        return {
            'total_items': len(products),
            'total_windows': len([p for p in products if 'W' in p.get('ref', '').upper()]),
            'total_doors': len([p for p in products if 'D' in p.get('ref', '').upper()]),
            'total_qty': sum(p.get('qty', 0) for p in products),
            'with_mosquito_net': len([p for p in products if p.get('insect_screen') == 'Yes'])
        }

# ============================================================================
# MAIN FUNCTION
# ============================================================================

def process_quotation_with_smart_detection(file_path: str) -> Dict[str, Any]:
    """
    ฟังก์ชันหลักสำหรับประมวลผลไฟล์ Quotation
    
    Args:
        file_path: เส้นทางไฟล์ Quotation (.pdf, .xlsx, .xls, .csv)
        
    Returns:
        Dict ที่มี:
        - success: True/False
        - data: ข้อมูลที่ประมวลผลแล้ว
        - message: ข้อความสถานะ
    """
    processor = EnhancedQuotationProcessor()
    return processor.process_quotation_file(file_path)