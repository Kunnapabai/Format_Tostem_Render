#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#use of text-glass2.html

import pdfplumber
import pandas as pd
import os
import json
import sys
import tempfile
import re
from typing import Dict, List, Optional

import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

class PDFExtractorWeb:
    def __init__(self):
        self.reference_code_data = []
        self.glass_data = []
        self.product_info = []
        self.site_survey_data = {}

    def clean_text(text: str) -> str:
        if not text:
            return text
        # Fix Thai character specifically first
        text = text.replace('เทมเปอร', 'เทมเปอร์')  # Fix missing thanthakhat
        text = text.replace('\uf71e', '\u0e4c')    # Fix encoding
        # Remove unwanted chars
        return re.sub(r'[^\u0E00-\u0E7Fa-zA-Z0-9\s\*\=\(\)\.\-]', '', text)
        
    def extract_site_survey_data(self, file_path: str) -> Dict[str, str]:
        glass_data = {}
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    text = page.extract_text()
                    if not text:
                        continue
                    
                    # หา Ref เช่น 3/16, 2/15, 3/18
                    ref_match = re.findall(r'\b\d+\/\d+\b', text)
                    # หา Glass เช่น [B]Ocean green 6 mm.
                    glass_match = re.findall(r'\[B\][^\n]*?\d+\s*mm\.', text)

                    if ref_match and glass_match:
                        for ref in ref_match:
                            glass_data[ref.strip()] = glass_match[-1].strip()

                    # Extract table data first - this is the most reliable method
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        
                        # หา column ของ Ref และ Glass
                        ref_col = glass_col = None
                        
                        # ตรวจสอบทุกแถวหา header
                        for i, row in enumerate(table):
                            if not row:
                                continue
                                
                            for j, cell in enumerate(row):
                                if cell and isinstance(cell, str):
                                    cell_lower = cell.lower().strip()
                                    
                                    # หา Ref column
                                    if 'ref' in cell_lower and ref_col is None:
                                        ref_col = j
                                    
                                    # หา Glass column
                                    glass_patterns = ['glass', 'กระจก']
                                    if any(pattern in cell_lower for pattern in glass_patterns) and glass_col is None:
                                        glass_col = j
                            
                            # ถ้าเจอ column แล้ว ให้ดึงข้อมูลจากแถวถัดไป
                            if ref_col is not None and glass_col is not None:
                                for data_row_idx in range(i + 1, len(table)):
                                    data_row = table[data_row_idx]
                                    if not data_row or len(data_row) <= max(ref_col, glass_col):
                                        continue
                                    
                                    ref_cell = data_row[ref_col] if ref_col < len(data_row) else None
                                    glass_cell = data_row[glass_col] if glass_col < len(data_row) else None
                                    
                                    if ref_cell and glass_cell:
                                        ref_code = str(ref_cell).strip()
                                        glass_type = str(glass_cell).strip()
                                        
                                        # **เพิ่มการอ่าน 2 บรรทัด - ตรวจสอบแถวถัดไป**
                                        if data_row_idx + 1 < len(table):
                                            next_row = table[data_row_idx + 1]
                                            if (next_row and glass_col < len(next_row) and 
                                                next_row[glass_col] and str(next_row[glass_col]).strip()):
                                                additional_glass = str(next_row[glass_col]).strip()
                                                # รวมข้อมูลจาก 2 บรรทัด
                                                glass_type = f"{glass_type} {additional_glass}"
                                        
                                        # ดึง reference codes ด้วย regex
                                        ref_matches = re.findall(r'([WDwd]\d+(?:\.\d+)?)', ref_code, re.IGNORECASE)
                                        
                                        if ref_matches and glass_type:
                                            # ทำความสะอาด glass_type และตรวจสอบว่ามี mm. หรือไม่
                                            glass_type = self._clean_glass_description(glass_type)
                                            
                                            if glass_type:  # ไม่เป็น None แสดงว่ามี mm.
                                                for ref_match in ref_matches:
                                                    ref_match = ref_match.upper()
                                                    glass_data[ref_match] = glass_type
                                break
        except Exception as e:
            print(f"Error extracting site survey data: {str(e)}", file=sys.stderr)
        
        return glass_data
        
    def _clean_glass_description(self, glass_desc: str) -> str:
        """ทำความสะอาดคำอธิบายกระจก รองรับ 2 บรรทัด"""
        if not glass_desc:
            return ""
        
        if not re.search(r'\d+\s*mm\.?', glass_desc, re.IGNORECASE):
            return None
        
        # แก้ไขปัญหา encoding ภาษาไทย
        glass_desc = glass_desc.replace('เทมเปอร', 'เทมเปอร์')
        glass_desc = glass_desc.replace('\uf71e', '\u0e4c')
        
        # เก็บเฉพาะอักษรไทย อังกฤษ ตัวเลข และสัญลักษณ์พื้นฐาน
        glass_desc = re.sub(r'[^\u0E00-\u0E7Fa-zA-Z0-9\s\.\-\+]', '', glass_desc)
        
        # จัดการ whitespace - รวมหลายช่องว่างเป็นช่องเดียว
        glass_desc = ' '.join(glass_desc.split())
        
        # ตัดข้อความที่ยาวเกินไป (ถ้าจำเป็น)
        if len(glass_desc) > 100:  # จำกัดความยาว
            glass_desc = glass_desc[:100].strip()
        
        return glass_desc
    
    def get_glass_type_from_site_survey(self, ref_code):
        """Enhanced glass type lookup with flexible matching"""
        if not ref_code or not self.site_survey_data:
            return None
            
        ref_code = str(ref_code).strip().upper()  # Normalize to uppercase
        
        # Direct match
        if ref_code in self.site_survey_data:
            return self.site_survey_data[ref_code]
        
        # Try without decimal part (W1.1 -> W1)
        if '.' in ref_code:
            base_ref = ref_code.split('.')[0]
            if base_ref in self.site_survey_data:
                return self.site_survey_data[base_ref]
        
        # Try with decimal parts (W1 -> look for W1.1, W1.2, etc.)
        if '.' not in ref_code:
            for key in self.site_survey_data.keys():
                if key.startswith(ref_code + '.'):
                    return self.site_survey_data[key]
        
        # Fuzzy matching - look for similar patterns
        for key in self.site_survey_data.keys():
            # Remove dots and compare
            if ref_code.replace('.', '') == key.replace('.', ''):
                return self.site_survey_data[key]
            
            # Check if either contains the other
            if ref_code in key or key in ref_code:
                return self.site_survey_data[key]
        
        return None
        
    def extract_data_from_file(self, file_path: str, start_page: int = 3, site_survey_path: Optional[str] = None) -> Dict:
        """Extract data from PDF file using the original logic"""
        self.reference_code_data = []
        self.glass_data = []
        self.product_info = []
        
        # Extract site survey data if provided
        if site_survey_path and os.path.exists(site_survey_path):
            self.site_survey_data = self.extract_site_survey_data(site_survey_path)
            print(f"Loaded site survey data: {self.site_survey_data}", file=sys.stderr)
        
        try:
            with pdfplumber.open(file_path) as pdf:
                start_idx = start_page - 1
                
                if start_idx >= len(pdf.pages):
                    return {"error": f"หน้าที่ {start_page} ไม่มีในไฟล์ PDF (มีทั้งหมด {len(pdf.pages)} หน้า)"}
                
                # Process each page from start_page
                for i in range(start_idx, len(pdf.pages)):
                    page = pdf.pages[i]

                    # 🟡 ดึงข้อความจากหน้า PDF
                    text = page.extract_text() or ""

                    # ✅ ข้ามหน้าที่มีคำว่า "Insect screen" หรือ "Screen"
                    if re.search(r'\b(insect\s*screen|screen)\b', text, re.IGNORECASE):
                        print(f"⏩ Skipped page {i+1} (contains Insect screen or Screen)", file=sys.stderr)
                        continue

                    tables = page.extract_tables()
                    
                    if tables:
                        for j, table in enumerate(tables):
                            # Extract product information
                            product_info = self._extract_product_info(table, i+1)
                            self.product_info.extend(product_info)
                            
                            # Extract reference and glass data
                            self._process_structured_table(table, i+1, j+1)
                
                return self._format_output()
                
        except Exception as e:
            return {"error": f"เกิดข้อผิดพลาดในการอ่าน PDF: {str(e)}"}

    def _process_structured_table(self, table: List, page_num: int, table_num: int):
        """Process table based on known structure from debug output"""
        if not table or len(table) < 6:
            return
        
        # Look for data rows - typically rows 5 and onwards contain actual data
        data_rows = []
        
        for row_idx, row in enumerate(table):
            if row_idx < 5:  # Skip header rows
                continue
            
            # Check if this is a data row (has meaningful content)
            if (row and len(row) >= 17 and 
                row[0] and str(row[0]).strip().isdigit() and  # First column is number
                row[1] and str(row[1]).strip()):  # Second column has reference code
                
                data_rows.append((row_idx, row))
        
        # Extract Reference Code and GLASS data from each data row
        for row_idx, row in data_rows:
            try:
                self._extract_row_data(row, row_idx, page_num)
            except Exception as e:
                pass  # Skip errors in web version
    
    def _extract_row_data(self, row: List, row_idx: int, page_num: int):
        """Extract Reference Code and GLASS data from a single row with intelligent pattern detection"""
        
        # Extract Reference Code data (same as original)
        ref_data = {
            'page': page_num,
            'row': row_idx,
            'No': str(row[0]).strip() if row[0] else '',
            'Reference_Code': str(row[1]).strip() if row[1] else '',
            'Wo': str(row[2]).strip() if len(row) > 2 and row[2] else '',
            'Ho': str(row[3]).strip() if len(row) > 3 and row[3] else '',
            'Name': str(row[4]).strip() if len(row) > 4 and row[4] else '',
            'AL': str(row[5]).strip() if len(row) > 5 and row[5] else '',
            'GLS': str(row[6]).strip() if len(row) > 6 and row[6] else '',
            'Width': str(row[7]).strip() if len(row) > 7 and row[7] else '',
            'Height': str(row[8]).strip() if len(row) > 8 and row[8] else '',
            'S_Spec': str(row[9]).strip() if len(row) > 9 and row[9] else '',
            'Order_Qty': str(row[11]).strip() if len(row) > 11 and row[11] else ''
        }
        
        # Only add if we have meaningful data
        if ref_data['No'] and ref_data['Reference_Code']:
            self.reference_code_data.append(ref_data)
        
        # Smart GLASS data extraction
        self._extract_glass_smart(row, row_idx, page_num)

    def _extract_product_info(self, table: List, page_num: int):
        """Extract Product name and Order Qty (sets) information"""
        product_info = []
        
        for row_idx, row in enumerate(table):
            if row and len(row) > 10:
                # Look for Product name pattern
                for i, cell in enumerate(row):
                    if cell and str(cell).strip() == 'Product name':
                        # Look for product code in the same row or next row
                        product_name = ''
                        order_qty = ''
                        
                        # Check same row for product code (usually a few columns after)
                        for j in range(i + 1, min(len(row), i + 10)):
                                cell_val = str(row[j]).strip() if row[j] else ''
                                if cell_val:
                                # This could be product code
                                    product_name = str(row[j]).strip()
                                    break
                        
                        # Look for Order Qty (sets) in the same table
                        for order_row_idx, order_row in enumerate(table):
                            if order_row:
                                for k, order_cell in enumerate(order_row):
                                    if order_cell and 'Order Qty' in str(order_cell):
                                        # Look for quantity value in nearby cells or next row
                                        # Check same row first
                                        for qty_idx in range(k - 2, min(len(order_row), k + 3)):
                                            if (qty_idx >= 0 and order_row[qty_idx] and 
                                                str(order_row[qty_idx]).strip().isdigit()):
                                                order_qty = str(order_row[qty_idx]).strip()
                                                break
                                        
                                        # If not found in same row, check next row
                                        if not order_qty and order_row_idx + 1 < len(table):
                                            next_row = table[order_row_idx + 1]
                                            if next_row and len(next_row) > k:
                                                for qty_idx in range(max(0, k - 2), min(len(next_row), k + 3)):
                                                    if (next_row[qty_idx] and 
                                                        str(next_row[qty_idx]).strip().isdigit()):
                                                        order_qty = str(next_row[qty_idx]).strip()
                                                        break
                                        break
                        
                        if product_name:
                            product_info.append({
                                'page': page_num,
                                'product_name': product_name,
                                'order_qty_sets': order_qty,
                                'message': f"Product name {product_name} มี Order Qty (sets) {order_qty if order_qty else 'ไม่พบข้อมูล'}"
                            })
                        break
        
        return product_info
    
    def _extract_glass_smart(self, row: List, row_idx: int, page_num: int):
        """Extract GLASS data using intelligent pattern recognition"""
        
        # Look for 4-digit numbers that could be GW/GH (glass dimensions)
        # and single digits that could be Qty
        potential_glass_data = []
        
        # Start looking after basic reference data (column 12+)
        start_col = 12
        
        for i in range(start_col, len(row)):
            if row[i] and str(row[i]).strip():
                value = str(row[i]).strip()
                
                # Check if this could be glass dimension (3-4 digit number)
                if value.isdigit() and len(value) >= 3:
                    potential_glass_data.append({
                        'index': i,
                        'value': value,
                        'type': 'dimension' if len(value) >= 3 else 'qty'
                    })
                # Check if this could be quantity (1-2 digit number)
                elif value.isdigit() and len(value) <= 2:
                    potential_glass_data.append({
                        'index': i,
                        'value': value,
                        'type': 'qty'
                    })
        
        # Group potential glass data into sets
        # Pattern: usually GW (4-digit), GH (4-digit), Qty (1-2 digit)
        glass_sets = self._group_glass_data(potential_glass_data)
        
        # Create glass entries
        ref_code = str(row[1]).strip() if row[1] else ''
        
        for set_num, glass_set in enumerate(glass_sets, 1):
            if glass_set:  # Only if we have data
                glass_data = {
                    'page': page_num,
                    'row': row_idx,
                    'ref_no': str(row[0]).strip() if row[0] else '',
                    'ref_code': ref_code,
                    'glass_set': set_num,
                    'GW': glass_set.get('gw', ''),
                    'GH': glass_set.get('gh', ''),
                    'Qty': glass_set.get('qty', '')
                }
                
                # Add glass type from site survey if available
                glass_type = self.get_glass_type_from_site_survey(ref_code)
                if glass_type:
                    glass_data['glass_type'] = glass_type
                    print(f"Found glass type for {ref_code}: {glass_type}".encode('utf-8', errors='replace').decode('utf-8'), file=sys.stderr)
                else:
                    print(f"No glass type found for {ref_code}", file=sys.stderr)
                
                # Only add if we have at least one meaningful value
                if glass_data['GW'] or glass_data['GH'] or glass_data['Qty']:
                    self.glass_data.append(glass_data)
    
    def _group_glass_data(self, potential_data):
        """Group potential glass data into logical sets (GW, GH, Qty)"""
        if not potential_data:
            return []
        
        glass_sets = []
        current_set = {}
        
        i = 0
        while i < len(potential_data):
            item = potential_data[i]
            
            # Look for pattern: dimension, dimension, qty
            # or just dimensions without qty
            if item['type'] == 'dimension':
                if not current_set:
                    # Start new set with first dimension (likely GW)
                    current_set['gw'] = item['value']
                elif 'gw' in current_set and 'gh' not in current_set:
                    # Second dimension (likely GH)
                    current_set['gh'] = item['value']
                    
                    # Look ahead for quantity
                    if (i + 1 < len(potential_data) and 
                        potential_data[i + 1]['type'] == 'qty'):
                        current_set['qty'] = potential_data[i + 1]['value']
                        i += 1  # Skip next item as we used it
                    
                    # Complete this set
                    glass_sets.append(current_set)
                    current_set = {}
                else:
                    # Start new set if current is full
                    if current_set:
                        glass_sets.append(current_set)
                    current_set = {'gw': item['value']}
            
            elif item['type'] == 'qty':
                if current_set and 'gh' in current_set:
                    # Add qty to current set
                    current_set['qty'] = item['value']
                    glass_sets.append(current_set)
                    current_set = {}
                elif not current_set:
                    # Standalone qty - might be for previous incomplete set
                    if glass_sets and 'qty' not in glass_sets[-1]:
                        glass_sets[-1]['qty'] = item['value']
            
            i += 1
        
        # Add any remaining set
        if current_set:
            glass_sets.append(current_set)
        
        return glass_sets
    
    def _format_output(self) -> Dict:
        """Format the extracted data"""
        # Create product info messages
        product_messages = []
        for info in self.product_info:
            product_messages.append(info['message'])
        
        return {
            'reference_code': self.reference_code_data,
            'glass_data': self.glass_data,
            'product_info': self.product_info,
            'product_messages': product_messages,
            'total_references': len(self.reference_code_data),
            'total_glass': len(self.glass_data),
            'site_survey_data': self.site_survey_data
        }

def get_order_qty_for_page(product_info, page_num):
    """Get Order Qty (sets) for a specific page"""
    for info in product_info:
        if info.get('page') == page_num:
            order_qty = info.get('order_qty_sets', '1')
            try:
                return int(order_qty) if order_qty and order_qty.isdigit() else 1
            except:
                return 1
    return 1

def generate_text_output(glass_data, product_info=None, site_survey_data=None):
    """Generate text format output grouped by glass type
    Format: 
    GlassType1
    RefCode GW * GH = Qty
    RefCode GW * GH = Qty
    GlassType2
    RefCode GW * GH = Qty
    Total Qty = X"""
    
    def remove_leading_zeros(value):
        """Remove leading zeros from numeric strings"""
        if not value or not str(value).strip():
            return value
        
        val_str = str(value).strip()
        if val_str.isdigit():
            return str(int(val_str))
        return val_str
    
    def clean_text(text: str) -> str:
        if not text:
            return text
        return re.sub(r'[^\u0E00-\u0E7Fa-zA-Z0-9\s\*\=\(\)\.\-]', '', text)

    def get_glass_type_from_survey(ref_code, survey_data):
        """Exact match glass type lookup only"""
        if not ref_code or not survey_data:
            return None
        ref_code = str(ref_code).strip().upper()
        if ref_code in survey_data:
            return survey_data[ref_code]
        return None
    
    if not glass_data or len(glass_data) == 0:
        return "ไม่พบข้อมูล GLASS ที่สมบูรณ์"
    
    # จัดกลุ่มข้อมูลตามชนิดกระจก
    glass_groups = {}
    total_qty = 0
    
    for glass_item in glass_data:
        ref_code = glass_item.get('ref_code', '').strip()
        gw = glass_item.get('GW', '').strip()
        gh = glass_item.get('GH', '').strip()
        qty = glass_item.get('Qty', '').strip()
        page_num = glass_item.get('page', 0)
        
        # เฉพาะข้อมูลที่สมบูรณ์
        if ref_code and gw and gh and qty:
            gw_clean = remove_leading_zeros(gw)
            gh_clean = remove_leading_zeros(gh)
            
            # Get Order Qty
            order_qty = get_order_qty_for_page(product_info or [], page_num)
            
            # Get glass type
            glass_type = get_glass_type_from_survey(ref_code, site_survey_data)
            
            if glass_type:
                glass_type = clean_text(glass_type)
            else:
                glass_type = ""  # Default สำหรับที่ไม่มี glass type
            
            # สร้าง entry
            try:
                qty_num = int(qty)
                total_qty_item = qty_num * order_qty
                total_qty += total_qty_item
                
                if order_qty > 1:
                    entry = f"{ref_code} {gw_clean} * {gh_clean} = {qty} * {order_qty} = {total_qty_item}"
                else:
                    entry = f"{ref_code} {gw_clean} * {gh_clean} = {qty}"
                
                # เพิ่มเข้ากลุ่ม
                if glass_type not in glass_groups:
                    glass_groups[glass_type] = []
                glass_groups[glass_type].append(entry)
                
            except:
                entry = f"{ref_code} {gw_clean} * {gh_clean} = {qty}"
                if glass_type not in glass_groups:
                    glass_groups[glass_type] = []
                glass_groups[glass_type].append(entry)
    
    # สร้างผลลัพธ์ (ตรงส่วนท้าย)
    if not glass_groups:
        return "ไม่พบข้อมูล GLASS ที่สมบูรณ์"

    content = ""

    # เรียงลำดับกลุ่ม - ให้กระจกที่มีชื่อมาก่อน
    sorted_groups = sorted(glass_groups.items(), key=lambda x: (x[0] == "", x[0]))

    for i, (glass_type, entries) in enumerate(sorted_groups):
        content += f"{glass_type}\n"
        for entry in entries:
            content += f"{entry}\n"
        
        # เว้นบรรทัดหลังจบแต่ละกลุ่ม (ยกเว้นกลุ่มสุดท้าย)
        if i < len(sorted_groups) - 1:
            content += "\n"

    content += f"\nTotal Qty = {total_qty}"

    return content

def save_results_to_files(result_data, output_folder='outputs'):
    """Save results to TXT and JSON files"""
    try:
        os.makedirs(output_folder, exist_ok=True)
        
        # Save TXT file with Order Qty multiplication and glass type
        txt_content = generate_text_output(
            result_data.get('glass_data', []),
            result_data.get('product_info', []),
            result_data.get('site_survey_data', {})
        )
        txt_file = os.path.join(output_folder, 'pdf_results.txt')
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(txt_content)
        
        # Save JSON file
        json_file = os.path.join(output_folder, 'pdf_results.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        
        return True
    except Exception as e:
        print(f"Error saving results: {e}", file=sys.stderr)
        return False

def main():
    """Main function for command line usage"""
    if len(sys.argv) < 4:
        print("Usage: python main3.py <pdf_file_path> <start_page> <job_id> [site_survey_path]", file=sys.stderr)
        sys.exit(1)
    
    pdf_file_path = sys.argv[1]
    start_page = int(sys.argv[2])
    job_id = sys.argv[3]
    site_survey_path = sys.argv[4] if len(sys.argv) > 4 else None
    
    # Check if PDF file exists
    if not os.path.exists(pdf_file_path):
        result = {"error": f"ไม่พบไฟล์ PDF: {pdf_file_path}"}
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)
    
    # Check if site survey file exists (if provided)
    if site_survey_path and not os.path.exists(site_survey_path):
        result = {"error": f"ไม่พบไฟล์ Site Survey: {site_survey_path}"}
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)
    
    # Initialize extractor and process PDF
    extractor = PDFExtractorWeb()
    
    try:
        result = extractor.extract_data_from_file(pdf_file_path, start_page, site_survey_path)
        
        # Save results to files if processing was successful
        if 'error' not in result:
            save_results_to_files(result)
        
        # Output JSON result to stdout for server.py to parse
        print(json.dumps(result, ensure_ascii=False))
        
    except Exception as e:
        error_result = {"error": f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}"}
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)

if __name__ == '__main__':
    main()