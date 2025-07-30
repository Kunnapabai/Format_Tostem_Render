#!/usr/bin/env python3
"""
main.py - PDF/TXT Quotation Comparator
รองรับทั้ง PDF vs PDF และ Text vs PDF
ระบบเปรียบเทียบข้อความ TXT กับไฟล์ PDF สำหรับใบเสนอราคา
Dependencies:
    pip install pdfplumber PyPDF2 argparse
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

# PDF libraries
try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False

try:
    import PyPDF2
    _HAS_PYPDF2 = True
except ImportError:
    _HAS_PYPDF2 = False


# ================== PDF Text Extraction Functions ==================

def extract_text_from_pdf(pdf_path: str, start_page: int = 1) -> str:
    """Extract text from PDF for comparison purposes"""
    if _HAS_PDFPLUMBER:
        texts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[start_page - 1:]:
                texts.append(page.extract_text() or "")
        return "\n".join(texts)
    elif _HAS_PYPDF2:
        texts = []
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages[start_page - 1:]:
                texts.append(page.extract_text() or "")
        return "\n".join(texts)
    else:
        raise RuntimeError("ติดตั้ง pdfplumber หรือ PyPDF2 ก่อนใช้งาน")


# ================== PDF Data Extraction for Structured Data ==================

class PDFExtractor:
    def __init__(self):
        self.glass_data = []
        
    def extract_structured_data_from_pdf(self, file_path: str, start_page: int = 3) -> Dict:
        """Extract structured data from PDF file for PDF vs PDF comparison"""
        self.glass_data = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                start_idx = start_page - 1
                
                if start_idx >= len(pdf.pages):
                    return {"error": f"หน้าที่ {start_page} ไม่มีในไฟล์ PDF (มีทั้งหมด {len(pdf.pages)} หน้า)"}
                
                # Process each page from start_page
                for i in range(start_idx, len(pdf.pages)):
                    page = pdf.pages[i]
                    tables = page.extract_tables()
                    
                    if tables:
                        for j, table in enumerate(tables):
                            self._process_structured_table(table, i+1, j+1)
                
                return self._format_output()
                
        except Exception as e:
            return {"error": f"เกิดข้อผิดพลาดในการอ่าน PDF: {str(e)}"}
    
    def _process_structured_table(self, table: List, page_num: int, table_num: int):
        """Process table based on known structure"""
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
        
        # Extract glass data from each data row
        for row_idx, row in data_rows:
            try:
                self._extract_row_data(row, row_idx, page_num)
            except Exception as e:
                pass  # Skip errors
    
    def _extract_row_data(self, row: List, row_idx: int, page_num: int):
        """Extract glass data from a single row"""
        # Smart GLASS data extraction
        self._extract_glass_smart(row, row_idx, page_num)

    def _extract_glass_smart(self, row: List, row_idx: int, page_num: int):
        """Extract GLASS data using intelligent pattern recognition"""
        
        # Look for numbers that could be GW/GH (glass dimensions) and Qty
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
        glass_sets = self._group_glass_data(potential_glass_data)
        
        # Create glass entries
        for set_num, glass_set in enumerate(glass_sets, 1):
            if glass_set:  # Only if we have data
                glass_data = {
                    'page': page_num,
                    'row': row_idx,
                    'ref_no': str(row[0]).strip() if row[0] else '',
                    'ref_code': str(row[1]).strip() if row[1] else '',
                    'glass_set': set_num,
                    'GW': glass_set.get('gw', ''),
                    'GH': glass_set.get('gh', ''),
                    'Qty': glass_set.get('qty', '')
                }
                
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
        return {
            'glass_data': self.glass_data,
            'total_glass': len(self.glass_data)
        }


def generate_text_from_glass_data(glass_data):
    """Generate text format output: RefCode GW * GH = Qty"""
    content = ""
    
    def remove_leading_zeros(value):
        """Remove leading zeros from numeric strings"""
        if not value or not str(value).strip():
            return value
        
        val_str = str(value).strip()
        
        if val_str.isdigit():
            return str(int(val_str))
        
        return val_str
    
    # Calculate total quantity
    total_qty = 0
    
    if glass_data:
        # Process each glass data entry - only include complete entries
        for glass_item in glass_data:
            ref_code = glass_item.get('ref_code', '').strip()
            gw = glass_item.get('GW', '').strip()
            gh = glass_item.get('GH', '').strip()
            qty = glass_item.get('Qty', '').strip()
            
            # Only create entry if we have ALL required data
            if ref_code and gw and gh and qty:
                # Remove leading zeros from GW and GH
                gw_clean = remove_leading_zeros(gw)
                gh_clean = remove_leading_zeros(gh)
                
                content += f"{ref_code} {gw_clean} * {gh_clean} = {qty}\n"
                
                # Add to total quantity
                try:
                    total_qty += int(qty)
                except ValueError:
                    pass
    
    # Add total quantity
    if content:
        content += f"Total Qty = {total_qty}\n"
    else:
        content = "ไม่พบข้อมูล GLASS ที่สมบูรณ์\n"
    
    return content


# ================== Text Parsing Functions ==================

def parse_txt_items(text: str):
    ITEM_RE = re.compile(r"""
        ^\s*
        (?P<code>[A-Z]+\d*[A-Z]*\d*[-\.]?\d*)\s+
        (?P<w>\d+)\s*[*xX]\s*(?P<h>\d+)\s*=\s*(?P<qty>\d+)
        \s*$
    """, re.VERBOSE)
    TOTAL_RE = re.compile(r"Total\s+Qty\s*=\s*(\d+)", re.IGNORECASE)

    items = []
    provided_total = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m_tot = TOTAL_RE.match(line)
        if m_tot:
            provided_total = int(m_tot.group(1))
            continue
        m = ITEM_RE.match(line)
        if m:
            d = m.groupdict()
            items.append({
                "code":     d["code"],
                "width":    int(d["w"]),
                "height":   int(d["h"]),
                "quantity": int(d["qty"])
            })
    return items, provided_total


def parse_pdf_items(text: str):
    """Parse PDF text by scanning for item patterns"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items = []

    for i, ln in enumerate(lines):
        # ลองหา pattern ที่ต่างกัน
        patterns = [
            # Pattern 1: seq + content + qty + w x h
            r"^(?P<seq>\d+)\s+.*?(?P<qty>\d+)\s+(?P<w>\d+)\s*[xX×]\s*(?P<h>\d+)",
            # Pattern 2: seq + qty + w x h (อาจจะไม่มี content ตรงกลาง)
            r"^(?P<seq>\d+)\s+.*?(?P<w>\d+)\s*[xX×]\s*(?P<h>\d+).*?(?P<qty>\d+)",
            # Pattern 3: รูปแบบอื่น
            r"^(?P<seq>\d+)\s+.*?(?P<w>\d+)\s*[xX×]\s*(?P<h>\d+).*?(?P<qty>\d+)"
        ]
        
        m = None
        for pattern in patterns:
            m = re.search(pattern, ln)
            if m:
                break
        
        if not m:
            continue

        try:
            seq = int(m.group("seq"))
            w   = int(m.group("w"))
            h   = int(m.group("h"))
            qty = int(m.group("qty"))
        except (ValueError, IndexError):
            continue

        # Search for code #D1.x in this line or next lines
        code = ""
        raw_lines = [ln]
        for j in range(i, min(i + 3, len(lines))):
            # หา pattern ของ code ที่หลากหลายขึ้น
            code_patterns = [
                r"#(D\d+\.\d+)",                      # #D1.1
                r"#([A-Z]+\d+\.\d+)",                 # #AD6A1.2  
                r"#([A-Z]+\d*-\d+)",                  # #AD3-1
                r"#([A-Z]\d+[A-Z]-\d+)",              # #D1A-1
                r"#([A-Z]+\d+[A-Z]+\d*)",             # #AD6A1, #AW8B1
                r"#([A-Z]+\d+[A-Z]+)",                # #AD3A
                r"#([A-Z]\d+[A-Z]\d*)",               # #D4A
                r"#([A-Z]+\d+)",                      # #AD1A
                r"#([A-Z]+\d*[A-Z]*\d*[-\.]?\d*)",    # รูปแบบทั่วไป
            ]
            
            for code_pattern in code_patterns:
                mc = re.search(code_pattern, lines[j])
                if mc:
                    code = mc.group(1)
                    if j != i:
                        raw_lines.append(lines[j])
                    break
            
            if code:
                break

        items.append({
            "seq":       seq,
            "code":      code,
            "quantity":  qty,
            "width":     w,
            "height":    h,
            "raw_lines": raw_lines
        })

    return items


# ================== Improved Comparison Functions ==================

def compare_items(txt_items, pdf_items, provided_total=None):
    """Compare lists of items and build differences and matched items - ใช้การค้นหาแบบ step-by-step"""
    diffs = []
    matched_count = 0
    
    # Debug: พิมพ์ข้อมูลที่ parse ได้
    print(f"DEBUG: txt_items = {len(txt_items)} items", file=sys.stderr)
    for i, item in enumerate(txt_items):
        print(f"  TXT[{i}]: {item}", file=sys.stderr)
    
    print(f"DEBUG: pdf_items = {len(pdf_items)} items", file=sys.stderr)
    for i, item in enumerate(pdf_items):
        print(f"  PDF[{i}]: {item}", file=sys.stderr)

    # สร้าง mapping ของ PDF items
    pdf_by_seq = {i["seq"]: i for i in pdf_items}
    used_pdf_sequences = set()  # เก็บ PDF sequences ที่ใช้แล้ว
    matched_txt_indices = set()  # เก็บ TXT indices ที่ match แล้ว
    
    # Helper function สำหรับเช็คว่า items ตรงกันไหม
    def items_match(txt_item, pdf_item):
        return (str(pdf_item["code"]).strip() == str(txt_item["code"]).strip() and
                int(pdf_item["width"]) == int(txt_item["width"]) and
                int(pdf_item["height"]) == int(txt_item["height"]) and
                int(pdf_item["quantity"]) == int(txt_item["quantity"]))
    
    # Helper function สำหรับเช็คว่า items มีความคล้ายคลึงไหม (เหมาะสมสำหรับแก้ไข)
    def items_similar(txt_item, pdf_item):
        """เช็คว่ารายการคล้ายกันพอที่จะเป็น 'แก้ไข' แทน 'เพิ่ม'"""
        similarities = 0
        total_checks = 4
        
        # เช็ค code
        if str(pdf_item["code"]).strip() == str(txt_item["code"]).strip():
            similarities += 2  # code สำคัญมาก
        
        # เช็ค width
        if int(pdf_item["width"]) == int(txt_item["width"]):
            similarities += 1
            
        # เช็ค height  
        if int(pdf_item["height"]) == int(txt_item["height"]):
            similarities += 1
            
        # เช็ค quantity
        if int(pdf_item["quantity"]) == int(txt_item["quantity"]):
            similarities += 1
        
        # ถ้าตรงกัน 3 จาก 5 หรือมากกว่า ถือว่าคล้ายกัน
        return similarities >= 3
    
    # Helper function สำหรับสร้าง notes การแก้ไข
    def generate_edit_notes(txt_item, pdf_item):
        notes = []
        if str(pdf_item["code"]).strip() != str(txt_item["code"]).strip():
            notes.append(f"แก้รหัสจาก {pdf_item['code']} เป็น {txt_item['code']}")
        if int(pdf_item["width"]) != int(txt_item["width"]):
            notes.append(f"แก้ความกว้างจาก {pdf_item['width']} เป็น {txt_item['width']}")
        if int(pdf_item["height"]) != int(txt_item["height"]):
            notes.append(f"แก้ความสูงจาก {pdf_item['height']} เป็น {txt_item['height']}")
        if int(pdf_item["quantity"]) != int(txt_item["quantity"]):
            notes.append(f"แก้จำนวนจาก {pdf_item['quantity']} เป็น {txt_item['quantity']}")
        return "; ".join(notes)
    
    print(f"DEBUG: Starting Step-by-Step Matching Process", file=sys.stderr)
    
    # ============ ขั้นตอนที่ 1: Sequence Matching (ทำทุกบรรทัด) ============
    print(f"DEBUG: === STEP 1: Sequence Matching ===", file=sys.stderr)
    
    for idx, txt_it in enumerate(txt_items, start=1):
        if idx in matched_txt_indices:
            continue
            
        src_code = txt_it["code"]
        src_w    = txt_it["width"]
        src_h    = txt_it["height"]
        src_q    = txt_it["quantity"]
        source_content = f"{src_code} {src_w} * {src_h} = {src_q}"
        
        print(f"DEBUG: Step1 - Processing TXT line {idx}: {source_content}", file=sys.stderr)
        
        # ลองหา PDF item ในตำแหน่งเดียวกัน (sequence matching)
        pdf_it = pdf_by_seq.get(idx)
        if pdf_it and pdf_it["seq"] not in used_pdf_sequences:
            print(f"DEBUG: Step1 - Trying sequence match {idx}: PDF seq {pdf_it['seq']}", file=sys.stderr)
            
            if items_match(txt_it, pdf_it):
                # ตรงกันทั้งหมด
                print(f"DEBUG: Step1 - Perfect sequence match found at {idx}", file=sys.stderr)
                matched_count += 1
                used_pdf_sequences.add(pdf_it["seq"])
                matched_txt_indices.add(idx)
                
                diffs.append({
                    "line_number":    pdf_it["seq"],
                    "type":           "correct",
                    "source_content": source_content,
                    "target_content": "\n".join(pdf_it["raw_lines"]),
                    "note":           "ข้อมูลถูกต้อง (Step 1: Sequence Match)",
                    "sort_priority":  1  # เพิ่ม sort priority
                })
            else:
                print(f"DEBUG: Step1 - Sequence position {idx} exists in PDF but doesn't match", file=sys.stderr)
        else:
            print(f"DEBUG: Step1 - No PDF item at sequence {idx} or already used", file=sys.stderr)
    
    print(f"DEBUG: Step1 completed - Matched: {len(matched_txt_indices)} items", file=sys.stderr)
    
    # ============ ขั้นตอนที่ 2: Flexible Search - Perfect Match (ทำทุกบรรทัดที่เหลือ) ============
    print(f"DEBUG: === STEP 2: Flexible Search (Perfect Match) ===", file=sys.stderr)
    
    for idx, txt_it in enumerate(txt_items, start=1):
        if idx in matched_txt_indices:
            continue
            
        src_code = txt_it["code"]
        src_w    = txt_it["width"]
        src_h    = txt_it["height"]
        src_q    = txt_it["quantity"]
        source_content = f"{src_code} {src_w} * {src_h} = {src_q}"
        
        print(f"DEBUG: Step2 - Processing TXT line {idx}: {source_content}", file=sys.stderr)
        
        # ค้นหาใน PDF sequences อื่นๆ (flexible matching)
        matching_pdf_item = None
        for pdf_item in pdf_items:
            if pdf_item["seq"] not in used_pdf_sequences and items_match(txt_it, pdf_item):
                matching_pdf_item = pdf_item
                break
        
        if matching_pdf_item:
            # พบรายการที่ตรงกันใน sequence อื่น
            print(f"DEBUG: Step2 - Flexible perfect match found: TXT {idx} matches PDF {matching_pdf_item['seq']}", file=sys.stderr)
            matched_count += 1
            used_pdf_sequences.add(matching_pdf_item["seq"])
            matched_txt_indices.add(idx)
            
            diffs.append({
                "line_number":    matching_pdf_item["seq"],
                "type":           "correct",
                "source_content": source_content,
                "target_content": "\n".join(matching_pdf_item["raw_lines"]),
                "note":           "ข้อมูลถูกต้อง",
                "sort_priority":  1  # เพิ่ม sort priority
            })
        else:
            print(f"DEBUG: Step2 - No perfect match found for TXT line {idx}", file=sys.stderr)
    
    print(f"DEBUG: Step2 completed - Total matched so far: {len(matched_txt_indices)} items", file=sys.stderr)
    
    # ============ ขั้นตอนที่ 3: Fallback Similar Search (ทำทุกบรรทัดที่เหลือ) ============
    print(f"DEBUG: === STEP 3: Fallback Similar Search ===", file=sys.stderr)
    
    for idx, txt_it in enumerate(txt_items, start=1):
        if idx in matched_txt_indices:
            continue
            
        src_code = txt_it["code"]
        src_w    = txt_it["width"]
        src_h    = txt_it["height"]
        src_q    = txt_it["quantity"]
        source_content = f"{src_code} {src_w} * {src_h} = {src_q}"
        
        print(f"DEBUG: Step3 - Processing TXT line {idx}: {source_content}", file=sys.stderr)
        
        # หาดรายการที่คล้ายกัน
        best_similar_pdf_item = None
        best_similarity = 0
        
        try:
            for pdf_item in pdf_items:
                if pdf_item["seq"] not in used_pdf_sequences:
                    similarities = 0
                    
                    # นับความคล้ายคลึง
                    if str(pdf_item["code"]).strip() == str(txt_it["code"]).strip():
                        similarities += 2  # code สำคัญมาก
                    if int(pdf_item["width"]) == int(txt_it["width"]):
                        similarities += 1
                    if int(pdf_item["height"]) == int(txt_it["height"]):
                        similarities += 1
                    if int(pdf_item["quantity"]) == int(txt_it["quantity"]):
                        similarities += 1
                    
                    print(f"DEBUG: Step3 - Comparing with PDF {pdf_item['seq']}: {pdf_item['code']} {pdf_item['width']}*{pdf_item['height']}={pdf_item['quantity']} (similarity: {similarities})", file=sys.stderr)
                    
                    # ถ้าคล้ายกันมากกว่า และมีอย่างน้อย 3 คะแนน
                    if similarities > best_similarity and similarities >= 3:
                        best_similarity = similarities
                        best_similar_pdf_item = pdf_item
            
            if best_similar_pdf_item:
                # พบรายการที่คล้ายกัน - แสดงเป็น "แก้ไข"
                print(f"DEBUG: Step3 - Similar match found: TXT {idx} similar to PDF {best_similar_pdf_item['seq']} (similarity: {best_similarity})", file=sys.stderr)
                used_pdf_sequences.add(best_similar_pdf_item["seq"])
                matched_txt_indices.add(idx)
                
                edit_notes = generate_edit_notes(txt_it, best_similar_pdf_item)
                
                diffs.append({
                    "line_number":    best_similar_pdf_item["seq"],
                    "type":           "modified",
                    "source_content": source_content,
                    "target_content": "\n".join(best_similar_pdf_item["raw_lines"]),
                    "note":           f"{edit_notes}",
                    "sort_priority":  2  # เพิ่ม sort priority
                })
            else:
                print(f"DEBUG: Step3 - No similar match found for TXT line {idx}", file=sys.stderr)
                
        except Exception as e:
            print(f"DEBUG: Step3 - Error processing TXT line {idx}: {str(e)}", file=sys.stderr)
    
    print(f"DEBUG: Step3 completed - Total processed: {len(matched_txt_indices)} items", file=sys.stderr)
    
    # ============ ขั้นตอนที่ 4: ตรวจสอบ PDF items ที่เหลือ (รายการเกิน) ============
    print(f"DEBUG: === STEP 4: Check for Extra PDF Items ===", file=sys.stderr)
    
    for pdf_it in pdf_items:
        if pdf_it["seq"] not in used_pdf_sequences:
            print(f"DEBUG: Step4 - Unused PDF item at seq {pdf_it['seq']} - extra item", file=sys.stderr)
            
            diffs.append({
                "line_number":    pdf_it["seq"],
                "type":           "removed",
                "source_content": "",
                "target_content": "\n".join(pdf_it["raw_lines"]),
                "note":           "รายการใน PDF เกินมา",
                "sort_priority":  4  # เพิ่ม sort priority
            })

    # ============ ขั้นตอนที่ 5: เพิ่มรายการที่หายไปในลำดับที่เหมาะสม ============
    print(f"DEBUG: === STEP 5: Add Missing TXT Items ===", file=sys.stderr)
    
    max_pdf_seq = max([p["seq"] for p in pdf_items], default=0)
    missing_counter = 1
    
    for idx, txt_it in enumerate(txt_items, start=1):
        if idx not in matched_txt_indices:
            src_code = txt_it["code"]
            src_w    = txt_it["width"]
            src_h    = txt_it["height"]
            src_q    = txt_it["quantity"]
            source_content = f"{src_code} {src_w} * {src_h} = {src_q}"
            
            print(f"DEBUG: Step5 - Adding missing TXT item {idx}: {source_content}", file=sys.stderr)
            
            diffs.append({
                "line_number":    max_pdf_seq + missing_counter,
                "type":           "added",
                "source_content": source_content,
                "target_content": "",
                "note":           f"เพิ่มรายการ {source_content}",
                "sort_priority":  3  # เพิ่ม sort priority
            })
            missing_counter += 1

    # ============ เปรียบเทียบยอดรวม ============
    txt_total = provided_total if provided_total is not None else sum(i["quantity"] for i in txt_items)
    pdf_total = sum(i["quantity"] for i in pdf_items)
    if txt_total != pdf_total:
        diffs.append({
            "line_number":    max(max_pdf_seq + missing_counter, max([d["line_number"] for d in diffs], default=0)) + 1,
            "type":           "modified",
            "source_content": f"Total Qty = {txt_total}",
            "target_content": f"Total Qty = {pdf_total}",
            "note":           "ยอดรวมไม่ตรง",
            "sort_priority":  2  # เพิ่ม sort priority
        })

    # เรียงลำดับผลลัพธ์ตาม sort_priority แล้วตาม line_number
    # Priority: 1=ถูก, 2=แก้ไข, 3=เพิ่ม, 4=ลบ
    diffs.sort(key=lambda x: (x.get("sort_priority", 999), x["line_number"]))

    # นับเฉพาะรายการที่แตกต่างจริงๆ (ไม่รวม "ถูก")
    actual_differences = [d for d in diffs if d["type"] != "correct"]

    # แยกรายการที่ถูกต้อง
    correct_items = [d for d in diffs if d["type"] == "correct"]

    print(f"DEBUG: Final Results - Matched: {matched_count}, Total Differences: {len(actual_differences)}", file=sys.stderr)

    return {
        "differences":   actual_differences,  # ส่งเฉพาะรายการที่แตกต่าง (เรียงตาม priority แล้ว)
        "matched_items": correct_items,       # รายการที่ถูก
        "matched_count": matched_count,
        "txt_total":     txt_total,
        "pdf_total":     pdf_total
    }


# ================== Main Processing Functions ==================

def process_pdf_vs_pdf_comparison(source_pdf_path: str, target_pdf_path: str, start_page: int = 3):
    """Process PDF vs PDF comparison"""
    try:
        # Extract structured data from source PDF
        extractor = PDFExtractor()
        source_result = extractor.extract_structured_data_from_pdf(source_pdf_path, start_page)
        
        if 'error' in source_result:
            return {"success": False, "error": f"ไม่สามารถอ่านไฟล์ PDF ต้นฉบับได้: {source_result['error']}"}
        
        # Generate text from extracted data
        source_glass_data = source_result.get('glass_data', [])
        source_text = generate_text_from_glass_data(source_glass_data)
        
        if not source_text.strip() or "ไม่พบข้อมูล" in source_text:
            return {"success": False, "error": "ไม่พบข้อมูลที่สามารถใช้เปรียบเทียบได้ในไฟล์ PDF ต้นฉบับ"}
        
        # Process comparison with target PDF
        return process_text_vs_pdf_comparison(source_text, target_pdf_path, 1)
        
    except Exception as e:
        return {"success": False, "error": f"เกิดข้อผิดพลาดในการประมวลผล PDF vs PDF: {str(e)}"}


def process_text_vs_pdf_comparison(text_block: str, pdf_path: str, start_page: int = 1):
    """Process Text vs PDF comparison"""
    try:
        pdf_text = extract_text_from_pdf(pdf_path, start_page)
        txt_items, provided_total = parse_txt_items(text_block)
        pdf_items = parse_pdf_items(pdf_text)
        cmp_res = compare_items(txt_items, pdf_items, provided_total)

        return {
            "success":        True,
            "matched_count":  cmp_res["matched_count"],
            "matched_items":  cmp_res["matched_items"],
            "parsing_info": {
                "source_items_detected":  len(txt_items),
                "target_items_detected":  len(pdf_items),
                "matched_items":          cmp_res["matched_count"],
                "source_total_qty":       cmp_res["txt_total"],
                "target_total_qty":       cmp_res["pdf_total"],
                "provided_total_qty":     provided_total
            },
            "totals": {
                "txt_total": cmp_res["txt_total"],
                "pdf_total": cmp_res["pdf_total"]
            },
            "differences":     cmp_res["differences"]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=['text_vs_pdf', 'pdf_vs_pdf'], required=True, 
                       help="โหมดการเปรียบเทียบ")
    
    # For text vs pdf mode
    parser.add_argument("--text", help="ข้อความ TXT ที่จะเปรียบเทียบ")
    
    # For pdf vs pdf mode  
    parser.add_argument("--source-pdf", help="ไฟล์ PDF ต้นฉบับ")
    parser.add_argument("--source-start-page", type=int, default=3, 
                       help="หน้าเริ่มต้นอ่าน PDF ต้นฉบับ")
    
    # Common arguments
    parser.add_argument("--target-pdf", required=True, help="ไฟล์ PDF เปรียบเทียบ")
    parser.add_argument("--target-start-page", type=int, default=1, 
                       help="หน้าเริ่มต้นอ่าน PDF เปรียบเทียบ")
    
    args = parser.parse_args()

    # Validate arguments based on mode
    if args.mode == 'text_vs_pdf' and not args.text:
        print(json.dumps({"error": "ต้องระบุ --text สำหรับโหมด text_vs_pdf"}), flush=True)
        sys.exit(1)
    
    if args.mode == 'pdf_vs_pdf' and not args.source_pdf:
        print(json.dumps({"error": "ต้องระบุ --source-pdf สำหรับโหมด pdf_vs_pdf"}), flush=True)
        sys.exit(1)

    # Check if target PDF exists
    if not Path(args.target_pdf).exists():
        print(json.dumps({"error": f"ไม่พบไฟล์ PDF เปรียบเทียบ: {args.target_pdf}"}), flush=True)
        sys.exit(1)

    # Check if source PDF exists (for pdf_vs_pdf mode)
    if args.mode == 'pdf_vs_pdf' and not Path(args.source_pdf).exists():
        print(json.dumps({"error": f"ไม่พบไฟล์ PDF ต้นฉบับ: {args.source_pdf}"}), flush=True)
        sys.exit(1)

    # Process based on mode
    if args.mode == 'text_vs_pdf':
        result = process_text_vs_pdf_comparison(args.text, args.target_pdf, args.target_start_page)
    else:  # pdf_vs_pdf
        result = process_pdf_vs_pdf_comparison(args.source_pdf, args.target_pdf, args.source_start_page)
    
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()