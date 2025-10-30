#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# use of Quotation.html

import sys
import os
import json
import re
import webbrowser
import threading
import time
import tempfile
from datetime import datetime
from typing import Dict, List, Any, Tuple

try:
    import pandas as pd
    from flask import Flask, request, jsonify
    from werkzeug.utils import secure_filename
    import pdfplumber
except ImportError as e:
    print(f"ไม่พบ module ที่จำเป็น: {e}")
    print("กรุณาติดตั้ง dependencies:")
    print("pip install Flask pandas pdfplumber Werkzeug")
    sys.exit(1)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

def sort_code(code: str):
    """
    Custom sort for window/door codes.
    Rules:
      - Prefix (เช่น W)
      - เลขหลัก (02 → แปลงเป็น int = 2)
      - Suffix:
          * ถ้าเป็น F, T (ไม่มีจุด) → มาก่อน
          * ถ้าเป็น .1, .1F, .2 → อยู่หลัง
    """
    match = re.match(r"([A-Za-z]+)(\d+)(.*)", code)
    if match:
        prefix, num, suffix = match.groups()
        # จัด priority: 0 = ไม่มีจุด (F, T), 1 = มีจุด (.1, .1F)
        suffix_priority = 1 if suffix.startswith('.') else 0
        return (prefix, int(num), suffix_priority, suffix)
    return (code, 0, 0, "")

class QuoteComparator:
    def __init__(self):
        self.quote_data = {}
        
    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract text from PDF file"""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text
        except Exception as e:
            print(f"Error extracting PDF: {e}")
            return ""
    
    def parse_quote_header(self, text: str) -> Dict[str, Any]:
        """Parse quote header information"""
        header = {}
        
        # Extract quote number with better pattern
        quote_patterns = [
            r'\(TM-(\d+)\)',
            r'TM-(\d+)',
            r'Quote.*?(\d{12,})',
        ]
        
        for pattern in quote_patterns:
            match = re.search(pattern, text)
            if match:
                header['quote_number'] = match.group(1)
                break
        
        # Extract date with multiple patterns
        date_patterns = [
            r'Date\s+(\d{4}-\d{2}-\d{2})',
            r'Date\s*:\s*(\d{4}-\d{2}-\d{2})',
            r'(\d{4}-\d{2}-\d{2})',
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                header['date'] = match.group(1)
                break
        
        # Extract customer name
        attn_patterns = [
            r'Attn\.\s*(.+?)(?:\s+Date|\n)',
            r'Attn\.?\s*(.+?)(?:\s|$)',
        ]
        
        for pattern in attn_patterns:
            match = re.search(pattern, text, re.MULTILINE)
            if match:
                header['customer'] = match.group(1).strip()
                break
        
        # Extract phone
        phone_patterns = [
            r'Tel\.\s*(\d+)',
            r'Tel:\s*(\d+)',
            r'โทร\s*(\d+)',
        ]
        
        for pattern in phone_patterns:
            match = re.search(pattern, text)
            if match:
                header['phone'] = match.group(1)
                break
        
        # Extract address
        addr_patterns = [
            r'Addr\.\s*(.+?)(?:\s+Tel|\n)',
            r'Address\s*(.+?)(?:\s+Tel|\n)',
        ]
        
        for pattern in addr_patterns:
            match = re.search(pattern, text, re.MULTILINE)
            if match:
                header['address'] = match.group(1).strip()
                break
        
        return header
    
    def parse_quote_items(self, text: str) -> List[Dict[str, Any]]:
        """Parse quote items from text with improved patterns"""
        items = []
        
        # Split text into lines for easier processing
        lines = text.split('\n')
        
        # Look for table patterns
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            code_pattern = r'^[A-Z][\w\.-]+\s+'
            has_price = '.' in line and any(char.isdigit() for char in line)
            has_thai = bool(re.search(r'[ก-๙]', line))
            
            # Better pattern to match item lines - look for Code at start
            if re.match(code_pattern, line) and (has_price or has_thai):
                # Split by multiple spaces or tabs to better separate columns
                parts = re.split(r'\s{2,}|\t+', line)
                
                # If that doesn't work well, try regular space split
                if len(parts) < 6:
                    parts = line.split()
                
                # Find price positions (numbers with .00)
                price_positions = []
                for j, part in enumerate(parts):
                    if re.match(r'[\d,]+\.00$', part):
                        price_positions.append(j)
                
                if len(parts) >= 6:
                    try:
                        # Extract basic info
                        code = parts[0]
                        series = parts[1] if len(parts) > 1 else ""
                        
                        # Get prices from the end
                        total_price = float(parts[-1].replace(',', ''))
                        unit_price = float(parts[-2].replace(',', ''))
                        
                        # Get quantity (usually second to last number before prices)
                        qty = 1
                        for j in range(len(parts) - 3, -1, -1):
                            if parts[j].isdigit() and int(parts[j]) <= 100:
                                qty = int(parts[j])
                                break
                        
                        # Extract dimensions (look for numbers that could be dimensions)
                        width = height = 0
                        dimension_candidates = []
                        
                        for j, part in enumerate(parts):
                            if part.isdigit():
                                num = int(part)
                                if 100 <= num <= 5000:
                                    if num > 100:
                                        dimension_candidates.append((j, num))
                        
                        # Sort by position and take last two as width/height
                        dimension_candidates.sort(key=lambda x: x[0])
                        if len(dimension_candidates) >= 2:
                            width = dimension_candidates[-2][1]  
                            height = dimension_candidates[-1][1] 
                        elif len(dimension_candidates) == 1:
                            width = dimension_candidates[0][1]
                        
                        # >>> ใส่ตรงนี้ <
                        # Extract description - everything that's not code, series, dimensions, quantity, or prices
                        desc_parts = []
                        dimension_positions = set([pos for pos, _ in dimension_candidates])
                        price_positions_set = set(price_positions)
                        
                        for j, part in enumerate(parts[1:], 1):  # Skip code (first element)
                            # Skip if this is series, dimension, price, or quantity
                            if (j == 1 and series and part == series) or \
                            (j in dimension_positions) or \
                            (j in price_positions_set) or \
                            (part.isdigit() and int(part) == qty and int(part) <= 100):
                                continue
                            
                            # Add to description
                            desc_parts.append(part)
                        
                        description = ' '.join(desc_parts)
                        
                        # Clean up description - remove extra whitespace
                        description = re.sub(r'\s+', ' ', description).strip()
                        # >>> จบการเพิ่ม <
                        
                        item = {
                            'code': code,
                            'series': series,
                            'description': description,
                            'width': width,
                            'height': height,
                            'quantity': qty,
                            'unit_price': unit_price,
                            'total_price': total_price
                        }
                        
                        # Validate item
                        if unit_price > 0 and total_price > 0:
                            items.append(item)
                            print(f"Parsed item: {code} - {description[:30]}... - W:{width} H:{height} Q:{qty} P:{unit_price}")
                            
                    except (ValueError, IndexError) as e:
                        print(f"Error parsing line: {line[:50]}... - {e}")
                        continue
        
        return items
    
    def parse_quote_summary(self, text: str) -> Dict[str, float]:
        """Parse quote summary with corrected duplicate character patterns"""
        summary = {}
        
        print(f"\n=== ENHANCED SUMMARY PARSING ===")
        
        # หาส่วนสรุป
        summary_keywords = ['ค่าติดตั้ง', 'ค่าขนส่ง', 'ราคารวม', 'ส่วนลด']
        summary_start = -1
        
        for keyword in summary_keywords:
            pos = text.find(keyword)
            if pos != -1:
                if summary_start == -1 or pos < summary_start:
                    summary_start = pos
        
        if summary_start == -1:
            return {}
        
        end_markers = ['รับทราบและตกลง', 'ผู้อนุมัติ', '_____']
        summary_end = len(text)
        
        for marker in end_markers:
            pos = text.find(marker, summary_start)
            if pos != -1 and pos < summary_end:
                summary_end = pos
        
        summary_section = text[summary_start:summary_end]
        print(f"Summary section: {repr(summary_section[:200])}")
        
        # Enhanced patterns รองรับตัวอักษรซ้ำมากขึ้น
        lines = summary_section.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
                
            prices_in_line = re.findall(r'([\d,]+\.00)', line)
            if not prices_in_line:
                continue
                
            price_value = float(prices_in_line[-1].replace(',', ''))
            print(f"Line {i}: {repr(line)} -> Price: {price_value:,.2f}")
            
            # Enhanced patterns รองรับ double/triple characters
            if 'รร' in line and 'คค' in line and 'สสิน' in line:
                summary['subtotal'] = price_value
                print(f"✅ Found subtotal: {price_value:,.2f}")

            elif 'สส' in line and 'ลลดด' in line and 'คค' in line and '%' in line:
                # จับทั้งเปอร์เซ็นต์และจำนวนเงิน
                pct_match = re.search(r'(\d+)\d*\s*%+', line)  # รองรับ 2200 %%
                amount_match = re.search(r'-\s*([\d,]+\.00)', line)
                
                if pct_match and amount_match:
                    # แปลง 2200 เป็น 20
                    rate_raw = pct_match.group(1)
                    if len(rate_raw) == 4 and rate_raw[:2] == rate_raw[2:]:  # 2200 -> 20
                        rate = float(rate_raw[:2])
                    else:
                        rate = float(rate_raw)
                    
                    summary['discount_rate'] = rate
                    summary['discount_amount'] = float(amount_match.group(1).replace(',', ''))
                    print(f"✅ Found discount: {rate}% = -{summary['discount_amount']:,.2f}")

            elif 'รร' in line and 'ส่ว' in line and 'ลลดด' in line:
                summary['discounted_total'] = price_value
                print(f"✅ Found discounted_total: {price_value:,.2f}")
                
            elif 'คค' in line and 'ตติด' in line:
                summary['installation_fee'] = price_value
                print(f"✅ Found installation_fee: {price_value:,.2f}")
                
            elif 'คค' in line and 'ขข' in line:
                summary['shipping_fee'] = price_value
                print(f"✅ Found shipping_fee: {price_value:,.2f}")
                
            elif 'รร' in line and 'สสุท' in line:
                summary['final_total'] = price_value
                print(f"✅ Found final_total: {price_value:,.2f}")
        
        print(f"FINAL SUMMARY: {summary}")
        return summary
    
    def load_quote(self, file_path: str, quote_id: str) -> Dict[str, Any]:
        """Load and parse a quote file"""
        text = self.extract_text_from_pdf(file_path)
        
        if not text.strip():
            raise ValueError(f"ไม่สามารถอ่านข้อมูลจากไฟล์ PDF ได้: {file_path}")
        
        # DEBUG: Print first 1000 characters of extracted text
        print(f"\n=== DEBUG {quote_id} - First 1000 chars of text ===")
        print(repr(text[:1000]))
        print("=" * 50)
        
        header = self.parse_quote_header(text)
        items = self.parse_quote_items(text)
        summary = self.parse_quote_summary(text)
        
        # DEBUG: Print parsed items
        print(f"\n=== DEBUG {quote_id} - Parsed {len(items)} items ===")
        for i, item in enumerate(items):
            print(f"Item {i+1}: {item['code']} | {item['series']} | {item['description'][:30]}... | W:{item['width']} H:{item['height']} Q:{item['quantity']} P:{item['unit_price']}")
        print("=" * 50)
        
        quote_data = {
            'file_path': file_path,
            'quote_id': quote_id,
            'header': header,
            'items': items,
            'summary': summary,
            'raw_text': text
        }
        
        self.quote_data[quote_id] = quote_data
        return quote_data
    
    def compare_quotes(self, quote_id1: str, quote_id2: str) -> Dict[str, Any]:
        """Compare two quotes and return differences"""
        if quote_id1 not in self.quote_data or quote_id2 not in self.quote_data:
            raise ValueError("One or both quote IDs not found")
        
        quote1 = self.quote_data[quote_id1]
        quote2 = self.quote_data[quote_id2]
        
        # Get item differences first
        item_differences = self._compare_items(quote1['items'], quote2['items'])
        
        # Calculate summary stats using unique codes method
        summary_stats = self._calculate_summary_stats(quote1['items'], quote2['items'])
        
        comparison = {
            'header_differences': self._compare_headers(quote1['header'], quote2['header']),
            'item_differences': item_differences,
            'summary_differences': self._compare_summaries(quote1['summary'], quote2['summary']),
            'summary_stats': summary_stats,  # Now uses unique code counting
            'quote1_info': {
                'id': quote_id1,
                'date': quote1['header'].get('date'),
                'quote_number': quote1['header'].get('quote_number')
            },
            'quote2_info': {
                'id': quote_id2,
                'date': quote2['header'].get('date'), 
                'quote_number': quote2['header'].get('quote_number')
            }
        }
        
        return comparison
    
    def _calculate_summary_stats(self, items1: List[Dict], items2: List[Dict]) -> Dict[str, int]:
        """Calculate summary statistics counting unique codes only (group by code)"""
        print(f"\n=== CALCULATE SUMMARY STATS (UNIQUE CODES) DEBUG ===")
        
        # Get unique codes from each file
        codes1 = set(item['code'] for item in items1)
        codes2 = set(item['code'] for item in items2)
        
        print(f"Unique codes in file1: {len(codes1)}")
        print(f"Unique codes in file2: {len(codes2)}")
        print(f"File1 codes: {sorted(codes1, key=sort_code)}")
        print(f"File2 codes: {sorted(codes2, key=sort_code)}")

        
        all_unique_codes = codes1 | codes2
        print(f"All unique codes: {len(all_unique_codes)}")
        
        # Count changes at the code level
        codes_modified = 0
        codes_added = 0
        codes_removed = 0
        
        # สำหรับการนับจำนวนรายการทั้งหมดที่ไม่ซ้ำ
        total_items_quote1 = len(codes1)
        total_items_quote2 = len(codes2)
        
        for code in all_unique_codes:
            exists_in_1 = code in codes1
            exists_in_2 = code in codes2
            
            print(f"\n--- Checking code: {code} ---")
            print(f"In file1: {exists_in_1}, In file2: {exists_in_2}")
            
            if exists_in_1 and exists_in_2:
                # Code exists in both files - aggregate and compare totals
                items1_for_code = [item for item in items1 if item['code'] == code]
                items2_for_code = [item for item in items2 if item['code'] == code]
                
                # Aggregate quantities and totals for the same code
                total_qty1 = sum(item.get('quantity', 0) for item in items1_for_code)
                total_qty2 = sum(item.get('quantity', 0) for item in items2_for_code)
                total_price1 = sum(item.get('total_price', 0) for item in items1_for_code)
                total_price2 = sum(item.get('total_price', 0) for item in items2_for_code)
                
                # Compare other fields from the first item of each code
                first_item1 = items1_for_code[0]
                first_item2 = items2_for_code[0]
                
                code_has_changes = False
                
                # Check if aggregated quantities or totals changed
                if not self.strict_compare_values(total_qty1, total_qty2):
                    code_has_changes = True
                    print(f"  Total quantity changed: {total_qty1} -> {total_qty2}")
                
                if not self.strict_compare_values(total_price1, total_price2):
                    code_has_changes = True
                    print(f"  Total price changed: {total_price1} -> {total_price2}")
                
                # Check other fields from representative items
                comparable_fields = ['series', 'description', 'width', 'height', 'unit_price']
                for field in comparable_fields:
                    val1 = first_item1.get(field)
                    val2 = first_item2.get(field)
                    if not self.strict_compare_values(val1, val2):
                        code_has_changes = True
                        print(f"  Field difference in {field}: '{val1}' vs '{val2}'")
                        break
                
                if code_has_changes:
                    codes_modified += 1
                    print(f"  RESULT: CODE {code} MODIFIED")
                else:
                    print(f"  RESULT: CODE {code} UNCHANGED")
                    
            elif exists_in_1 and not exists_in_2:
                codes_removed += 1
                print(f"  RESULT: CODE {code} REMOVED")
                
            elif not exists_in_1 and exists_in_2:
                codes_added += 1
                print(f"  RESULT: CODE {code} ADDED")
        
        print(f"\n=== FINAL UNIQUE CODE STATS ===")
        print(f"Total unique codes file1: {total_items_quote1}")
        print(f"Total unique codes file2: {total_items_quote2}")
        print(f"Unique codes modified: {codes_modified}")
        print(f"Unique codes added: {codes_added}")
        print(f"Unique codes removed: {codes_removed}")
        
        # คำนวณจำนวนรหัสซ้ำในแต่ละไฟล์
        duplicate_codes1 = len(items1) - total_items_quote1
        duplicate_codes2 = len(items2) - total_items_quote2
        
        print(f"Duplicate entries in file1: {duplicate_codes1}")
        print(f"Duplicate entries in file2: {duplicate_codes2}")
        print("================================")
        
        return {
            'total_items_quote1': total_items_quote1,  # จำนวน unique codes
            'total_items_quote2': total_items_quote2,  # จำนวน unique codes
            'total_entries_quote1': len(items1),       # จำนวนรายการทั้งหมด (รวมซ้ำ)
            'total_entries_quote2': len(items2),       # จำนวนรายการทั้งหมด (รวมซ้ำ)
            'duplicate_codes_quote1': duplicate_codes1, # จำนวนรหัสซ้ำในไฟล์ 1
            'duplicate_codes_quote2': duplicate_codes2, # จำนวนรหัสซ้ำในไฟล์ 2
            'items_modified': codes_modified,
            'items_added': codes_added,
            'items_removed': codes_removed,
        }

    def _compare_headers(self, header1: Dict, header2: Dict) -> List[Dict]:
        """Compare header information"""
        differences = []
        all_keys = set(header1.keys()) | set(header2.keys())
        
        for key in all_keys:
            val1 = header1.get(key, "")
            val2 = header2.get(key, "")
            
            if val1 != val2:
                differences.append({
                    'field': key,
                    'quote1_value': val1,
                    'quote2_value': val2
                })
        
        return differences
    
    def strict_compare_values(self, val1, val2, field_name="unknown"):
        """Improved comparison that handles different data types exactly"""
        # Handle null/undefined cases
        if val1 is None and val2 is None:
            return True
        if val1 is None or val2 is None:
            print(f"DEBUG: {field_name} - One value is None: '{val1}' vs '{val2}'")
            return False
        
        # For numeric values, use exact comparison (with small tolerance for floating point)
        if isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
            result = abs(float(val1) - float(val2)) < 0.01
            if not result:
                print(f"DEBUG: {field_name} - Numeric difference: {val1} vs {val2}")
            return result
        
        # For strings, normalize and compare exactly
        str1 = str(val1).strip().replace('\u00a0', ' ').replace('\t', ' ')
        str2 = str(val2).strip().replace('\u00a0', ' ').replace('\t', ' ')
        
        # Normalize whitespace
        str1 = re.sub(r'\s+', ' ', str1)
        str2 = re.sub(r'\s+', ' ', str2)
        
        result = str1 == str2
        if not result:
            print(f"DEBUG: {field_name} - String difference: '{str1}' vs '{str2}'")
        
        return result
    
    def _compare_items(self, items1: List[Dict], items2: List[Dict]) -> Dict:
        """Enhanced item comparison with better matching - includes ALL items and handles duplicates"""
        
        # Create unique keys for items with duplicate codes by adding index
        items1_dict = {}
        items2_dict = {}
        
        # Handle duplicate codes by creating unique keys
        for i, item in enumerate(items1):
            code = item['code']
            existing_codes = [existing_item['code'] for existing_item in items1[:i]]
            unique_key = f"{code}_{existing_codes.count(code)}" if code in existing_codes else code
            item['unique_key'] = unique_key
            items1_dict[unique_key] = item
        
        for i, item in enumerate(items2):
            code = item['code']
            existing_codes = [existing_item['code'] for existing_item in items2[:i]]
            unique_key = f"{code}_{existing_codes.count(code)}" if code in existing_codes else code
            item['unique_key'] = unique_key
            items2_dict[unique_key] = item
        
        all_keys = set(items1_dict.keys()) | set(items2_dict.keys())
        
        differences = {
            'modified_items': [],
            'added_items': [],
            'removed_items': [],
            'unchanged_items': [],  # Add this for items that match exactly
            'price_changes': [],
            'all_items': []  # Add this to send all items to frontend
        }
        
        print(f"Comparing {len(all_keys)} unique items...")
        
        for unique_key in sorted(all_keys, key=sort_code):  # Sort for consistent display
            item1 = items1_dict.get(unique_key)
            item2 = items2_dict.get(unique_key)
            
            # Use the original code for display, but unique_key for processing
            display_code = item1['code'] if item1 else item2['code']
            
            # Create item entry for all_items list
            item_entry = {
                'code': display_code,
                'unique_key': unique_key,
                'item1': item1,
                'item2': item2,
                'status': 'unknown'
            }
            
            if item1 and item2:
                # Both items exist - check for differences
                has_changes = False
                item_differences = []
                
                comparable_fields = ['series', 'description', 'width', 'height', 'quantity', 'unit_price', 'total_price']
                
                for field in comparable_fields:
                    val1 = item1.get(field)
                    val2 = item2.get(field)
                    
                    if not self.strict_compare_values(val1, val2):
                        has_changes = True
                        item_differences.append({
                            'field': field,
                            'old_value': val1,
                            'new_value': val2
                        })
                        print(f"Difference in {display_code}.{field}: '{val1}' -> '{val2}'")
                
                if has_changes:
                    differences['modified_items'].append({
                        'code': display_code,
                        'unique_key': unique_key,
                        'has_changes': True,
                        'differences': item_differences,
                        'item1': item1,
                        'item2': item2
                    })
                    item_entry['status'] = 'modified'
                    
                    # Track price changes
                    if not self.strict_compare_values(item1.get('unit_price'), item2.get('unit_price')):
                        old_price = float(item1.get('unit_price', 0))
                        new_price = float(item2.get('unit_price', 0))
                        differences['price_changes'].append({
                            'code': display_code,
                            'unique_key': unique_key,
                            'old_price': old_price,
                            'new_price': new_price,
                            'price_change': new_price - old_price,
                            'percent_change': ((new_price - old_price) / old_price * 100) if old_price > 0 else 0
                        })
                else:
                    # Item exists in both files and is exactly the same
                    differences['unchanged_items'].append({
                        'code': display_code,
                        'unique_key': unique_key,
                        'item1': item1,
                        'item2': item2
                    })
                    item_entry['status'] = 'unchanged'
                    print(f"Item {display_code} matches perfectly")
            
            elif item1 and not item2:
                item1['unique_key'] = unique_key  # Add unique_key to item
                differences['removed_items'].append(item1)
                item_entry['status'] = 'removed'
                print(f"Item {display_code} was removed (exists only in file1)")
            
            elif not item1 and item2:
                item2['unique_key'] = unique_key  # Add unique_key to item
                differences['added_items'].append(item2)
                item_entry['status'] = 'added'
                print(f"Item {display_code} was added (exists only in file2)")
            
            # Add to all_items list
            differences['all_items'].append(item_entry)
        
        print(f"Comparison results: {len(differences['modified_items'])} modified, {len(differences['unchanged_items'])} unchanged, {len(differences['added_items'])} added, {len(differences['removed_items'])} removed")
        
        return differences
    
    def _compare_summaries(self, summary1: Dict, summary2: Dict) -> List[Dict]:
        """Compare summary totals - แสดงทั้งหมดแม้ค่าเท่ากัน"""
        differences = []
        
        # กำหนดฟิลด์ที่ต้องการแสดงทั้งหมด
        all_fields = ['subtotal', 'discount_amount', 'discounted_total', 
                    'installation_fee', 'shipping_fee', 'final_total']
        
        # รวมฟิลด์ทั้งหมดที่มีในทั้งสองไฟล์
        all_keys = set(summary1.keys()) | set(summary2.keys())
        
        # เรียงลำดับฟิลด์ตามความสำคัญ
        ordered_keys = []
        for field in all_fields:
            if field in all_keys:
                ordered_keys.append(field)
        
        # เพิ่มฟิลด์อื่นๆ ที่ไม่ได้กำหนดไว้
        for key in all_keys:
            if key not in ordered_keys:
                ordered_keys.append(key)
        
        for key in ordered_keys:
            val1 = summary1.get(key, 0)
            val2 = summary2.get(key, 0)
            
            # แสดงทุกฟิลด์ ไม่ว่าจะต่างกันหรือไม่
            differences.append({
                'field': key,
                'quote1_value': val1,
                'quote2_value': val2,
                'difference': val2 - val1
            })
        
        return differences

# Flask routes
@app.route('/')
def index():
    try:
        with open('index6.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "ไม่พบไฟล์ index6.html", 404

@app.route('/api/process-quotation', methods=['POST'])
def process_quotation():
    try:
        if 'file1' not in request.files or 'file2' not in request.files:
            return jsonify({'error': 'กรุณาอัปโหลดไฟล์ PDF 2 ไฟล์'}), 400
        
        file1 = request.files['file1']
        file2 = request.files['file2']
        
        if file1.filename == '' or file2.filename == '':
            return jsonify({'error': 'กรุณาเลือกไฟล์'}), 400
        
        # Save uploaded files temporarily
        temp_dir = tempfile.mkdtemp()
        file1_path = os.path.join(temp_dir, secure_filename(file1.filename))
        file2_path = os.path.join(temp_dir, secure_filename(file2.filename))
        
        file1.save(file1_path)
        file2.save(file2_path)
        
        # Process quotes
        quote_comp = QuoteComparator()
        
        quote1_data = quote_comp.load_quote(file1_path, 'quote1')
        quote2_data = quote_comp.load_quote(file2_path, 'quote2')
        
        comparison = quote_comp.compare_quotes('quote1', 'quote2')
        
        # Cleanup temp files
        os.unlink(file1_path)
        os.unlink(file2_path)
        os.rmdir(temp_dir)
        
        return jsonify(comparison)
        
    except Exception as e:
        print(f"Error processing quotation: {e}")
        return jsonify({'error': str(e)}), 500

# Global comparator instance
comparator = QuoteComparator()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)