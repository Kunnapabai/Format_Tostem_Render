# main.py - Standalone TXT vs PDF Checker
import argparse
import json
import re
import sys
from pathlib import Path
import pdfplumber

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()

def extract_pdf_text(pdf_file_path: str, start_page: int = 1) -> str:
    with pdfplumber.open(pdf_file_path) as pdf:
        # อ่านจากหน้าที่กำหนดถึงหน้าสุดท้าย
        pages_to_read = pdf.pages[start_page-1:] if start_page > 1 else pdf.pages
        return "\n".join((p.extract_text() or "") for p in pages_to_read)

ITEM_RE = re.compile(
    r"""
    (?P<code>D\d+\.\d+)\s+            # D1.1 / D1.2
    (?P<w>\d+)\s*[*xX]\s*(?P<h>\d+)\s*# 897 * 2060
    =\s*(?P<qty>\d+)                  # = 4
    """, re.VERBOSE
)

def parse_lines(text_block: str):
    items = []
    for raw in text_block.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = ITEM_RE.search(raw)
        if m:
            d = m.groupdict()
            d["raw"] = raw
            items.append(d)
        else:
            items.append({"raw": raw, "error": "format ไม่ถูก"})
    return items

def check_item_in_pdf(item, pdf_text_norm: str):
    if "error" in item:
        return False, "format ไม่ถูก"

    code, w, h, qty = item["code"], item["w"], item["h"], item["qty"]
    
    # ตรวจสอบแต่ละส่วนแยกกัน
    missing_parts = []
    found_parts = []
    
    # ตรวจสอบ code (D1.1) - รองรับหลายรูปแบบ
    code_patterns = [
        rf"\b{re.escape(code)}\b",           # D1.1
        rf"#{re.escape(code)}\b",            # #D1.1  
        rf"#{re.escape(code)}(?!\w)",        # #D1.1 (ไม่ตามด้วยตัวอักษร)
        rf"\b{re.escape(code)}(?!\w)",       # D1.1 (ไม่ตามด้วยตัวอักษร)
    ]
    code_found = False
    for pattern in code_patterns:
        if re.search(pattern, pdf_text_norm, re.IGNORECASE):
            code_found = True
            found_parts.append(f"รหัส '{code}'")
            break
    
    if not code_found:
        missing_parts.append(f"รหัส '{code}' (ลองหา: {code}, #{code})")
    
    # ตรวจสอบ width - ต้องตรงกันทุกหลัก
    width_patterns = [
        rf"\b{w}\b",                         # 897 (exact match)
        rf"(?<!\d){w}(?!\d)",               # 897 ไม่มีตัวเลขติดข้างหน้าหรือหลัง
        rf"{w}\s*[xX*×]\s*\d+",             # 897 ตามด้วย X และตัวเลข
    ]
    width_found = False
    for pattern in width_patterns:
        if re.search(pattern, pdf_text_norm, re.IGNORECASE):
            # ตรวจสอบเพิ่มเติมว่าไม่ใช่ส่วนของเลขที่ยาวกว่า
            matches = re.findall(rf"(\d*){w}(\d*)", pdf_text_norm)
            for before, after in matches:
                if not before and not after:  # ไม่มีตัวเลขติดข้างหน้าและหลัง
                    width_found = True
                    break
            if width_found:
                break
    
    if width_found:
        found_parts.append(f"ความกว้าง '{w}'")
    else:
        missing_parts.append(f"ความกว้าง '{w}' (อาจพบ {w}xxx หรือ xxx{w} แต่ไม่ใช่ {w} เท่านั้น)")
    
    # ตรวจสอบ height - ต้องตรงกันทุกหลัก  
    height_patterns = [
        rf"\b{h}\b",                         # 2060 (exact match)
        rf"(?<!\d){h}(?!\d)",               # 2060 ไม่มีตัวเลขติดข้างหน้าหรือหลัง
        rf"[xX*×]\s*{h}(?!\d)",             # 2060 หลัง X
    ]
    height_found = False
    for pattern in height_patterns:
        if re.search(pattern, pdf_text_norm, re.IGNORECASE):
            # ตรวจสอบเพิ่มเติมว่าไม่ใช่ส่วนของเลขที่ยาวกว่า
            matches = re.findall(rf"(\d*){h}(\d*)", pdf_text_norm)
            for before, after in matches:
                if not before and not after:  # ไม่มีตัวเลขติดข้างหน้าและหลัง
                    height_found = True
                    break
            if height_found:
                break
    
    if height_found:
        found_parts.append(f"ความสูง '{h}'")
    else:
        missing_parts.append(f"ความสูง '{h}' (อาจพบ {h}xxx หรือ xxx{h} แต่ไม่ใช่ {h} เท่านั้น)")
    
    # ตรวจสอบ quantity - ต้องตรงกันทุกหลัก
    qty_patterns = [
        rf"\b{qty}\b",                       # 4 (exact match) 
        rf"(?<!\d){qty}(?!\d)",             # 4 ไม่มีตัวเลขติดข้างหน้าหรือหลัง
        rf"^\s*{qty}(?!\d)",                # 4 ที่จุดเริ่มบรรทัด
        rf"\s{qty}(?!\d)\s",                # 4 ล้อมรอบด้วยช่องว่าง
    ]
    qty_found = False
    for pattern in qty_patterns:
        if re.search(pattern, pdf_text_norm, re.IGNORECASE | re.MULTILINE):
            # ตรวจสอบเพิ่มเติมว่าไม่ใช่ส่วนของเลขที่ยาวกว่า
            matches = re.findall(rf"(\d*){qty}(\d*)", pdf_text_norm)
            for before, after in matches:
                if not before and not after:  # ไม่มีตัวเลขติดข้างหน้าและหลัง
                    qty_found = True
                    break
            if qty_found:
                break
    
    if qty_found:
        found_parts.append(f"จำนวน '{qty}'")
    else:
        missing_parts.append(f"จำนวน '{qty}' (อาจพบ {qty}xxx หรือ xxx{qty} แต่ไม่ใช่ {qty} เท่านั้น)")
    
    if missing_parts:
        reason = f"ไม่พบ: {', '.join(missing_parts)}"
        if found_parts:
            reason += f" | พบ: {', '.join(found_parts)}"
        return False, reason
    
    # ถ้าทุกส่วนพบแล้ว ตรวจสอบว่าอยู่ใกล้กันหรือไม่
    context_patterns = [
        rf"{qty}.*?{w}\s*[x*×]\s*{h}",      # 4 ... 897 X 2060
        rf"{w}\s*[x*×]\s*{h}.*?{qty}",      # 897 X 2060 ... 4
        rf"{code}.*?{w}\s*[x*×]\s*{h}.*?{qty}",        # D1.1 ... 897 X 2060 ... 4
        rf"#{code}.*?{w}\s*[x*×]\s*{h}.*?{qty}",       # #D1.1 ... 897 X 2060 ... 4
        rf"{qty}.*?{w}\s*[x*×]\s*{h}.*?#{code}",       # 4 ... 897 X 2060 ... #D1.1
    ]
    context_found = any(re.search(pattern, pdf_text_norm, re.IGNORECASE | re.DOTALL) for pattern in context_patterns)
    
    if context_found:
        return True, f"พบครบทุกส่วนและสัมพันธ์กัน: {', '.join(found_parts)}"
    
    return True, f"พบครบทุกส่วนแต่อาจกระจาย: {', '.join(found_parts)}"

def main():
    parser = argparse.ArgumentParser(description='TXT vs PDF Checker')
    parser.add_argument('--text', required=True, help='Text to search for')
    parser.add_argument('--pdf', required=True, help='PDF file path')
    parser.add_argument('--start-page', type=int, default=1, help='Starting page number')
    parser.add_argument('--show-pdf-content', action='store_true', help='Include PDF content in output')
    
    args = parser.parse_args()
    
    try:
        # ตรวจสอบไฟล์ PDF
        if not Path(args.pdf).exists():
            print(json.dumps({"error": f"ไม่พบไฟล์ PDF: {args.pdf}"}))
            sys.exit(1)
        
        # ดึงข้อความจาก PDF
        pdf_text_raw = extract_pdf_text(args.pdf, args.start_page)
        pdf_text_norm = normalize(pdf_text_raw)
        
        # แยก input text
        items = parse_lines(args.text)
        
        # ตรวจสอบแต่ละบรรทัด
        results = []
        for item in items:
            ok, reason = check_item_in_pdf(item, pdf_text_norm)
            results.append({
                "raw": item.get("raw"),
                "ok": ok,
                "reason": reason
            })
        
        # เตรียมผลลัพธ์
        output = {
            "results": results,
            "total_lines": len(results),
            "found_lines": sum(1 for r in results if r["ok"]),
            "missing_lines": sum(1 for r in results if not r["ok"])
        }
        
        # เพิ่มเนื้อหา PDF ถ้าต้องการ
        if args.show_pdf_content:
            output["pdf_content"] = pdf_text_raw[:5000]  # แสดงแค่ 5000 ตัวอักษรแรก
            output["pdf_content_full_length"] = len(pdf_text_raw)
        
        # ส่งผลลัพธ์เป็น JSON
        print(json.dumps(output, ensure_ascii=False, indent=2))
        
    except Exception as e:
        print(json.dumps({"error": f"เกิดข้อผิดพลาด: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()